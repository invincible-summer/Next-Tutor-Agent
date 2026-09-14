// M2/M3/M5/M6 只读投影 + M7 评估 + M4 CAT 测评的 API 客户端。
// 基础连接模型与 api.ts 一致（API_BASE 单一事实源，SSE 直连后端）。
import { API_BASE } from "./api";
import { apiFetch } from "./api-fetch";
import type {
  AssessmentActiveResp,
  AssessmentAnswerResp,
  AssessmentNextResp,
  AssessmentStartResp,
  ConceptDetailResp,
  CustomDeleteResp,
  CustomGraphListResp,
  EpisodesResp,
  EvalProposal,
  EvalGuidanceEntry,
  EvalReport,
  EvalTrace,
  AssessmentQuestion,
  ContextBudgetReport,
  EvalConceptsResp,
  WorkspacesEvaluationResp,
  EvalEvidenceDetail,
  EvalJobDetail,
  ConceptEvaluationView,
  EvalSessionEvidenceItem,
  WorkspaceEvaluationSummary,
  EvalTimelineResp,
  EvalSessionsResp,
  KnowledgeCatalogResp,
  KnowledgeGraphResp,
  KnowledgeTaxonomyResp,
  LearningPathResp,
  OrchDailyTask,
  OrchGoalResp,
  OrchHabit,
  OrchPlanSummary,
  OrchReviewItem,
  OrchSubTask,
  OrchTaskResp,
  OrchWeekConceptResp,
  OrchWeekResp,
  OrchWeekTask,
  ProceduralResp,
  RecentQuizResp,
  ErrorNotebookResp,
  DocsContentResp,
  SemanticResp,
  StudentProfileResp,
  TeachingLogResp,
} from "./types-modules";

const get = <T>(path: string): Promise<T> => apiFetch(`${API_BASE}${path}`).then((r) => r.json());

// --- M2/M3 学生模型 & 教学引擎（只读投影） ---

export const getStudentProfile = (studentId = "student_default") =>
  get<StudentProfileResp>(`/student/profile?student_id=${encodeURIComponent(studentId)}`);

export const getTeachingLog = (studentId = "student_default") =>
  get<TeachingLogResp>(`/student/teaching-log?student_id=${encodeURIComponent(studentId)}`);

export const getLearningPath = (studentId = "student_default", workspaceId = "") => {
  const q = new URLSearchParams({ student_id: studentId });
  if (workspaceId) q.set("workspace_id", workspaceId);
  return get<LearningPathResp>(`/student/learning-path?${q}`);
};

// --- M5 知识图谱（只读投影） ---

export interface KnowledgeGraphQuery {
  textbookId?: string;
  fileId?: string;
  level?: string;
  subject?: string;
  view?: "full" | "overview" | "chapter" | "search";
  chapterId?: string;
  search?: string;
  /** 当前工作区（§11.6/§14.4）：带区强制与 scope 求交并附个人评价。 */
  workspaceId?: string;
}

export const getKnowledgeGraph = (studentId = "student_default", options: string | KnowledgeGraphQuery = "") => {
  const q = new URLSearchParams({ student_id: studentId });
  const query = typeof options === "string" ? { textbookId: options } : options;
  if (query.textbookId) q.set("textbook_id", query.textbookId);
  if (query.fileId) q.set("file_id", query.fileId);
  if (query.level) q.set("level", query.level);
  if (query.subject) q.set("subject", query.subject);
  if (query.view) q.set("view", query.view);
  if (query.chapterId) q.set("chapter_id", query.chapterId);
  if (query.search) q.set("q", query.search);
  if (query.workspaceId) q.set("workspace_id", query.workspaceId);
  return get<KnowledgeGraphResp>(`/knowledge/graph?${q}`);
};

export const getKnowledgeTaxonomy = (studentId = "student_default") =>
  get<KnowledgeTaxonomyResp>(`/knowledge/taxonomy?student_id=${encodeURIComponent(studentId)}`);

export const getConceptDetail = (conceptId: string, studentId = "student_default", workspaceId = "") => {
  const q = new URLSearchParams({ student_id: studentId });
  if (workspaceId) q.set("workspace_id", workspaceId);
  return get<ConceptDetailResp>(
    `/knowledge/concepts/${encodeURIComponent(conceptId)}?${q}`,
  );
};

// --- M5.7 自定义知识图谱 + M5.8 学科目录 ---

export const getCustomGraphs = (studentId = "student_default") =>
  get<CustomGraphListResp>(`/knowledge/custom?student_id=${encodeURIComponent(studentId)}`);

export const getKnowledgeCatalog = (studentId = "student_default") =>
  get<KnowledgeCatalogResp>(`/knowledge/catalog?student_id=${encodeURIComponent(studentId)}`);

export async function deleteCustomGraph(topicKey: string): Promise<CustomDeleteResp> {
  const res = await apiFetch(
    `${API_BASE}/knowledge/custom/${encodeURIComponent(topicKey)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`DELETE custom graph failed: ${res.status}`);
  return res.json();
}

// --- M6 记忆（只读投影） ---

export const getEpisodes = (limit = 50, before?: number, studentId = "student_default") => {
  const q = new URLSearchParams({ student_id: studentId, limit: String(limit) });
  if (before) q.set("before", String(before));
  return get<EpisodesResp>(`/memory/episodes?${q}`);
};

export const getSemanticMemory = (studentId = "student_default") =>
  get<SemanticResp>(`/memory/semantic?student_id=${encodeURIComponent(studentId)}`);

export const getProceduralMemory = (studentId = "student_default") =>
  get<ProceduralResp>(`/memory/procedural?student_id=${encodeURIComponent(studentId)}`);

/** 跨会话最近习题（测评中心列表；student_id 由鉴权解析，无需传参）。 */
export const getRecentQuizQuestions = () =>
  get<RecentQuizResp>(`/quiz/recent`);

/** 错题本（跨会话聚合答错/半对的题，测评中心错题本卡）。 */
export const getErrorNotebook = () =>
  get<ErrorNotebookResp>(`/student/error-notebook`);

// --- 统一学习评价（/learner-evaluation/*，plan §11.2；全部走 apiFetch） ---

/** 本人工作区卡片（无证据的工作区也出现）。 */
export const getEvalWorkspaces = (offset = 0, limit = 20) =>
  get<WorkspacesEvaluationResp>(
    `/learner-evaluation/workspaces?offset=${offset}&limit=${limit}`);

/** 工作区学习档案总览：叙述/覆盖/水位/pending。 */
export const getEvalWorkspace = (workspaceId: string) =>
  get<WorkspaceEvaluationSummary>(
    `/learner-evaluation/workspaces/${encodeURIComponent(workspaceId)}`);

/** 当前范围概念投影（未观察节点由 scope 左连接生成）。 */
export const getEvalConcepts = (
  workspaceId: string,
  opts: { state?: string; textbookId?: string; q?: string; offset?: number; limit?: number } = {},
) => {
  const p = new URLSearchParams();
  if (opts.state) p.set("state", opts.state);
  if (opts.textbookId) p.set("textbook_id", opts.textbookId);
  if (opts.q) p.set("q", opts.q);
  p.set("offset", String(opts.offset ?? 0));
  p.set("limit", String(opts.limit ?? 50));
  return get<EvalConceptsResp>(
    `/learner-evaluation/workspaces/${encodeURIComponent(workspaceId)}/concepts?${p}`);
};

/** 单概念当前主张/条件/变化/next_probe（concept_key = ConceptRef.key）。 */
export const getEvalConceptDetail = (workspaceId: string, conceptKey: string) =>
  get<ConceptEvaluationView>(
    `/learner-evaluation/workspaces/${encodeURIComponent(workspaceId)}/concepts/${encodeURIComponent(conceptKey)}`);

/** 本区对话（有历史来源的会话行，无证据也有行）。 */
export const getEvalSessions = (workspaceId: string, offset = 0, limit = 20) =>
  get<EvalSessionsResp>(
    `/learner-evaluation/workspaces/${encodeURIComponent(workspaceId)}/sessions?offset=${offset}&limit=${limit}`);

/** 单会话在本区发生的观察（历史归属按 §5.3）。 */
export const getEvalSessionEvidence = (workspaceId: string, sourceSessionRef: string) =>
  get<{ items: EvalSessionEvidenceItem[]; total: number }>(
    `/learner-evaluation/workspaces/${encodeURIComponent(workspaceId)}/sessions/${encodeURIComponent(sourceSessionRef)}`);

/** 可定位证据时间线（按概念/来源种类/会话过滤）。 */
export const getEvalEvidence = (
  workspaceId: string,
  opts: { conceptKey?: string; sourceKind?: string; sourceSessionRef?: string; offset?: number; limit?: number } = {},
) => {
  const p = new URLSearchParams();
  if (opts.conceptKey) p.set("concept_key", opts.conceptKey);
  if (opts.sourceKind) p.set("source_kind", opts.sourceKind);
  if (opts.sourceSessionRef) p.set("source_session_ref", opts.sourceSessionRef);
  p.set("offset", String(opts.offset ?? 0));
  p.set("limit", String(opts.limit ?? 50));
  return get<EvalTimelineResp>(
    `/learner-evaluation/workspaces/${encodeURIComponent(workspaceId)}/evidence?${p}`);
};

/** 单条证据明细：原始表现、题目公开/揭晓视图、解释、复核记录。 */
export const getEvalEvidenceDetail = (sourceId: string) =>
  get<EvalEvidenceDetail>(`/learner-evaluation/evidence/${encodeURIComponent(sourceId)}`);

/** 作业状态（无原始模型输出）。 */
export const getEvalJob = (jobId: string) =>
  get<EvalJobDetail>(`/learner-evaluation/jobs/${encodeURIComponent(jobId)}`);

/** 「评价不准确」→ C9 复核（§14.6：同源只允许一个 active review）。 */
export const createEvalReview = (
  sourceId: string,
  body: { interpretation_id: string; reason: string; issue_kind?: string; expected_revision: number },
) =>
  post<{ review_id: string; job_id: string; duplicate?: boolean }>(
    `/learner-evaluation/evidence/${encodeURIComponent(sourceId)}/reviews`, body);

/** 失败作业手动重试（§10.3：另开 parent_job_id 新作业）。 */
export const retryEvalJob = (jobId: string, expectedRevision = 0) =>
  post<{ job_id: string; parent_job_id: string }>(
    `/learner-evaluation/jobs/${encodeURIComponent(jobId)}/retry`,
    { expected_revision: expectedRevision });

/** 触发工作区综合（§12.5：仅重组织有效证据）。 */
export const requestEvalSynthesis = (workspaceId: string, expectedScopeRevision = "") =>
  post<{ job_id: string; duplicate?: boolean }>(
    `/learner-evaluation/workspaces/${encodeURIComponent(workspaceId)}/synthesis`,
    { expected_scope_revision: expectedScopeRevision });

/** 删除一条证据（§14.6：物理清除来源副本、失效重综合）。 */
export async function deleteEvalEvidence(sourceId: string): Promise<void> {
  const res = await apiFetch(
    `${API_BASE}/learner-evaluation/evidence/${encodeURIComponent(sourceId)}`,
    { method: "DELETE" },
  );
  if (!res.ok && res.status !== 202) {
    throw new Error(`DELETE evidence failed: ${res.status}`);
  }
}

// --- 使用文档（/docs：全员读、管理员写） ---

export const getDocsContent = () =>
  get<DocsContentResp>("/docs/content");

export async function putDocsContent(markdown: string): Promise<DocsContentResp> {
  const res = await apiFetch(`${API_BASE}/docs/content`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown }),
  });
  if (!res.ok) throw new Error(`PUT docs failed: ${res.status}`);
  return res.json();
}

// --- M7 评估与改进（既有端点） ---

export const getEvalReport = (studentId = "student_default") =>
  get<EvalReport>(`/evaluation/report?student_id=${encodeURIComponent(studentId)}`);

export const getEvalTraces = (limit = 50, studentId = "student_default") =>
  get<EvalTrace[]>(`/evaluation/traces?student_id=${encodeURIComponent(studentId)}&limit=${limit}`);

export const getEvalProposals = (studentId = "student_default") =>
  get<EvalProposal[]>(`/evaluation/proposals?student_id=${encodeURIComponent(studentId)}`);

export const getContextBudgetReport = (limit = 200, studentId = "student_default") =>
  get<ContextBudgetReport>(`/evaluation/context-budget?student_id=${encodeURIComponent(studentId)}&limit=${limit}`);

/** 生效中/已吊销的教学指导（M7 提案应用后进入 M3 教学引擎）。 */
export const getEvalGuidance = () =>
  get<EvalGuidanceEntry[]>("/evaluation/guidance");

/** 吊销一条教学指导（立即回滚，条目保留审计）。 */
export const revokeEvalGuidance = (id: string) =>
  del<{ entry_id: string; active: boolean }>(
    `/evaluation/guidance/${encodeURIComponent(id)}`);

export async function patchProposal(
  id: string,
  status: "approved" | "rejected" | "applied",
  studentId = "student_default",
): Promise<unknown> {
  const res = await apiFetch(
    `${API_BASE}/evaluation/proposals/${encodeURIComponent(id)}?student_id=${encodeURIComponent(studentId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    },
  );
  if (!res.ok) throw new Error(`Patch proposal failed: ${res.status}`);
  return res.json();
}

// --- M4 CAT 自适应测评（既有端点，前端首次接入） ---

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} failed: ${res.status}`);
  return res.json();
}

/** §11.5：concept_keys=ConceptRef.key 列表（1–20）；purpose=用户任务意图。 */
export interface AssessmentStartPayload {
  workspace_id?: string;
  concept_keys: string[];
  goal?: { purpose?: "adaptive" | "diagnose" | "practice"; target_claims?: string[] };
  q_type?: string;
  count?: number;
  probe_ref?: Record<string, unknown>;
  expected_scope_revision?: string;
  grade?: string;
  subject?: string;
}

export const assessmentStart = (body: AssessmentStartPayload) =>
  post<AssessmentStartResp>("/assessment/start", body);

/** 一次正式提交（§11.4/§11.5）：题目身份由服务端校验，不能只靠活动槽位。 */
export const assessmentAnswer = (body: {
  assessment_id: string;
  question_id: string;
  question_revision: number;
  student_answer: string;
}) => post<AssessmentAnswerResp>("/assessment/answer", body);

export const assessmentNext = (assessmentId: string, expectedRevision = 0) =>
  post<AssessmentNextResp>("/assessment/next", {
    assessment_id: assessmentId,
    expected_revision: expectedRevision,
  });

export const assessmentReport = (assessmentId = "") => {
  const q = assessmentId ? `?assessment_id=${encodeURIComponent(assessmentId)}` : "";
  return get<{ status: string; summary?: unknown }>(`/assessment/report${q}`);
};

/** W2/A03 恢复端点：当前选区 active 实例或已持久化的终止状态。 */
export const assessmentActive = (workspaceId = "") => {
  const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
  return get<AssessmentActiveResp>(`/assessment/active${q}`);
};

export const assessmentAbandon = (assessmentId: string) =>
  post<{ status: string }>("/assessment/abandon", {
    assessment_id: assessmentId,
  });

/** 关键步骤提示（服务端记录帮助事件；§7.3）。 */
export async function assessmentHint(questionId: string, questionRevision: number) {
  const res = await apiFetch(
    `${API_BASE}/assessment/questions/${encodeURIComponent(questionId)}/hint`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_revision: questionRevision }),
    },
  );
  if (!res.ok) throw new Error(`Hint failed: ${res.status}`);
  return res.json() as Promise<{ status: string; hint: string }>;
}

/** 揭晓（服务端记录 answer_revealed；§14.5）。 */
export async function assessmentReveal(questionId: string, questionRevision: number) {
  const res = await apiFetch(
    `${API_BASE}/assessment/questions/${encodeURIComponent(questionId)}/reveal`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_revision: questionRevision }),
    },
  );
  if (!res.ok) throw new Error(`Reveal failed: ${res.status}`);
  return res.json() as Promise<{
    status: string; answer: string; explanation: string; already_answered: boolean;
  }>;
}

/** 再练一次（§11.5）：新 question instance（same/variant）。 */
export const assessmentPractice = (
  questionId: string,
  body: { question_revision: number; mode?: "same" | "variant"; expected_scope_revision?: string },
) =>
  post<{ status: string; question?: AssessmentQuestion }>(
    `/assessment/questions/${encodeURIComponent(questionId)}/practice`, body);

// --- M9 学习编排（目标/周计划/今日任务/SRS/习惯/模拟；读端点无 status 信封） ---

export const getOrchPlan = () => get<OrchPlanSummary>("/orchestration/plan");

export const getOrchToday = () => get<OrchDailyTask[]>("/orchestration/today");

export const getOrchHabit = () => get<OrchHabit>("/orchestration/habit");

export const getOrchReview = () => get<OrchReviewItem[]>("/orchestration/review");

export interface OrchGoalPayload {
  title?: string;
  description?: string;
  goal_type?: string;
  subjects?: string[];
  /** 概念级绑定（L1 目标链）：图谱概念 id 列表 */
  target_concept_ids?: string[];
  deadline?: number;
}

export const setOrchGoal = (body: OrchGoalPayload & { title: string }) =>
  post<OrchGoalResp>("/orchestration/goal", body);

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await apiFetch(`${API_BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`PATCH ${path} failed: ${res.status}`);
  return res.json();
}

export const patchOrchGoal = (goalId: string, body: OrchGoalPayload) =>
  patch<OrchGoalResp>(
    `/orchestration/goal/${encodeURIComponent(goalId)}`, body);

export const deleteOrchGoal = (goalId: string) =>
  del<{ ok: boolean }>(`/orchestration/goal/${encodeURIComponent(goalId)}`);

export const patchOrchSchedule = (dailyMinutes: number) =>
  patch<{ ok: boolean; schedule: { daily_minutes: number } }>(
    "/orchestration/schedule", { daily_minutes: dailyMinutes });

export interface OrchTaskCreatePayload {
  /** YYYY-MM-DD */
  day: string;
  title?: string;
  concept_id?: string;
  concept_name?: string;
  kind: string;
  phase?: string;
  estimate_minutes?: number;
  priority?: number;
  milestone_id?: string;
}

export type OrchTaskPatchPayload = Partial<OrchTaskCreatePayload> & { status?: string };

export const addOrchTask = (body: OrchTaskCreatePayload) =>
  post<OrchTaskResp>("/orchestration/task", body);

export const updateOrchTask = (taskId: string, body: OrchTaskPatchPayload) =>
  patch<{ ok: boolean }>(`/orchestration/task/${encodeURIComponent(taskId)}`, body);

export async function deleteOrchTask(taskId: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(
    `${API_BASE}/orchestration/task/${encodeURIComponent(taskId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`DELETE orch task failed: ${res.status}`);
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await apiFetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE ${path} failed: ${res.status}`);
  return res.json();
}

// --- M9 计划层级增删（周/周内概念/周任务/子任务） ---

export interface OrchWeekTaskPayload {
  title: string;
  concept_ids?: string[];
  kind?: string;
}

export const addOrchWeekTask = (weekIndex: number, body: OrchWeekTaskPayload) =>
  post<{ ok: boolean; task: OrchWeekTask }>(
    `/orchestration/week/${weekIndex}/task`, body);

export const deleteOrchWeekTask = (weekIndex: number, taskId: string) =>
  del<{ ok: boolean }>(
    `/orchestration/week/${weekIndex}/task/${encodeURIComponent(taskId)}`);

export interface OrchSubtaskPayload {
  title: string;
  estimate_minutes?: number;
}

export const addOrchSubtask = (weekIndex: number, taskId: string, body: OrchSubtaskPayload) =>
  post<{ ok: boolean; subtask: OrchSubTask }>(
    `/orchestration/week/${weekIndex}/task/${encodeURIComponent(taskId)}/subtask`, body);

export async function toggleOrchSubtask(weekIndex: number, taskId: string, subtaskId: string) {
  const res = await apiFetch(
    `${API_BASE}/orchestration/week/${weekIndex}/task/${encodeURIComponent(taskId)}/subtask/${encodeURIComponent(subtaskId)}`,
    { method: "PATCH" },
  );
  if (!res.ok) throw new Error(`PATCH subtask failed: ${res.status}`);
  return res.json() as Promise<{ ok: boolean }>;
}

export const deleteOrchSubtask = (weekIndex: number, taskId: string, subtaskId: string) =>
  del<{ ok: boolean }>(
    `/orchestration/week/${weekIndex}/task/${encodeURIComponent(taskId)}/subtask/${encodeURIComponent(subtaskId)}`);

export const suggestOrchSubtasks = (weekIndex: number, taskId: string) =>
  post<{ ok: boolean; task: OrchWeekTask }>(
    `/orchestration/week/${weekIndex}/task/${encodeURIComponent(taskId)}/suggest`, {});

export interface OrchWeekConceptPayload {
  concept_id?: string;
  name?: string;
  difficulty?: number;
  milestone_id?: string;
}

export interface OrchWeekPayload {
  focus?: string;
  concepts?: OrchWeekConceptPayload[];
  week_start?: number;
}

export const addOrchWeek = (body: OrchWeekPayload) =>
  post<OrchWeekResp>("/orchestration/week", body);

export const deleteOrchWeek = (weekIndex: number) =>
  del<{ ok: boolean }>(`/orchestration/week/${weekIndex}`);

export const addOrchWeekConcept = (weekIndex: number, body: OrchWeekConceptPayload) =>
  post<OrchWeekConceptResp>(`/orchestration/week/${weekIndex}/concept`, body);

export const removeOrchWeekConcept = (weekIndex: number, conceptId: string) =>
  del<{ ok: boolean }>(`/orchestration/week/${weekIndex}/concept/${encodeURIComponent(conceptId)}`);

export const completeOrchTask = (taskId: string) =>
  post<{ ok: boolean; emitted_events: number }>(
    `/orchestration/task/${encodeURIComponent(taskId)}/complete`,
    {},
  );

/** W4/A12：服务端任务启动绑定——校验归属、预创建携带 task_binding 的会话，
 * 返回 launch_url；未完成任务的重复 launch 幂等复用同一会话。 */
export const launchOrchTask = (taskId: string) =>
  post<import("./types-modules").OrchLaunchResp>(
    `/orchestration/task/${encodeURIComponent(taskId)}/launch`,
    {},
  );

// --- M0 账户资料（/user/profile，需登录；guest 模式前端不调用） ---
export const getUserProfile = () =>
  get<{ status: string; profile: import("./types-modules").UserProfileData }>("/user/profile");

export async function updateUserProfile(
  body: Partial<import("./types-modules").UserProfileData>,
): Promise<import("./types-modules").UserProfileData> {
  const res = await apiFetch(`${API_BASE}/user/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Update profile failed: ${res.status}`);
  const data = await res.json();
  return data.profile;
}

/** 注销当前账号（需密码二次确认）。成功后服务端删除账户记录，JWT 随之失效。 */
export async function deleteAccount(password: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/user/account`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `Delete account failed: ${res.status}`);
  }
}
