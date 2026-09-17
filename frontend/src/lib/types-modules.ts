import type { QuestionIllustrationData } from "./types";
// M2/M3/M5/M6 只读投影端点的响应类型（后端 api/v1/{student,knowledge,memory}.py）。

export interface StudentProfileData {
  id: string;
  grade: string;
  subjects: string[];
  learning_style: { preference: string; explanation_depth: string };
  goals: string[];
  created_at: number;
  updated_at: number;
  last_active: number;
  events_processed: number;
}

export interface StudentProfileResp {
  status: string;
  profile: StudentProfileData | null;
}

export interface TeachingLogEntry {
  mode: string;
  outcome: string;
  ts: number;
  note: string;
}

export interface TeachingLogConcept {
  current_mode: string;
  current_outcome: string;
  last_ts: number;
  entries: TeachingLogEntry[];
}

export interface TeachingLogResp {
  status: string;
  concepts: Record<string, TeachingLogConcept>;
}

export interface PathNode {
  skill_id?: string;
  concept?: string;
  name?: string;
  subject?: string;
  difficulty?: number;
  reason?: string;
  [key: string]: unknown;
}

export interface LearningPathResp {
  status: string;
  next_to_learn: PathNode[];
  /** G4：待复习=已观察待解决（emerging/fragile/conflicting），state 随行。 */
  review: PathNode[];
  difficulty?: number | null;
  rationale?: string;
}

// --- M5 知识图谱 ---

/** 图谱节点上的统一评价 overlay（§11.6/§14.4；章节节点固定 null）。 */
export interface NodeEvaluation {
  state: string | null;
  statement: string;
  judgment_id: string;
  concept_key: string;
  evaluation_status: string;
  updated_at: string;
}

export interface KnowledgeNode {
  id: string;
  name: string;
  subject: string;
  level: string;
  difficulty: number;
  description: string;
  aliases: string[];
  common_errors: string[];
  evaluation?: NodeEvaluation | null;
  /** concept | chapter | section（章节容器靠 part_of 收编子概念，不参与检索；
   *  节 = 章内二级结构如课/篇目/小节，参与名称检索但不追踪掌握度）；旧包无此字段 */
  kind?: "concept" | "chapter" | "section" | string;
  /** 节点来源（如 seed 考纲包）；旧包无此字段 */
  origin?: string;
  /** 章节节点携带 file_id / volume_id / chapter_order 等；概念节点含所属章节/卷列表 */
  metadata?: Record<string, unknown>;
}

export interface KnowledgeEdge {
  from: string;
  to: string;
  type: "prerequisite" | "related" | "part_of" | "application" | "misconception" | string;
}

export interface KnowledgeGraphResp {
  status: string;
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
  learned_edges?: number;
  coverage?: KnowledgeVolumeCoverage[];
  view?: "full" | "overview" | "chapter" | "search" | string;
  scope?: { level?: string; subject?: string; textbook_id?: string; file_id?: string };
}

export interface KnowledgeVolumeCoverage {
  file_id: string;
  name?: string;
  status: string;
  error?: string;
  truncated?: boolean;
  extracted_chapter_count?: number;
  extracted_concept_count?: number;
  included_chapter_count?: number;
  included_concept_count?: number;
  effective_limits?: { max_chapters?: number | null; max_concepts?: number | null };
}

export interface ConceptDetailResp {
  status: string;
  concept: KnowledgeNode & { content?: unknown };
  edges: {
    prerequisites: KnowledgeNode[];
    unlocks: KnowledgeNode[];
    /** PART_OF 上行：概念的所属课文/单元、课文的所属单元 */
    parents: KnowledgeNode[];
    /** PART_OF 下行：课文/单元下的成员（概念或课文） */
    children: KnowledgeNode[];
    related: KnowledgeNode[];
    applications: KnowledgeNode[];
    misconceptions: KnowledgeNode[];
  };
  evaluation?: NodeEvaluation | null;
  teaching_log: TeachingLogEntry[];
  memories: Episode[];
}

// --- M5.7 自定义知识图谱 + M5.8 学科目录 ---

export interface CustomGraphMeta {
  topic: string;
  topic_key: string;
  subject: string;
  level: string;
  version: number;
  /** llm | material:xxx */
  source: string;
  /** 后端直出 ISO 字符串（可能为空串） */
  created_at: string;
  updated_at: string;
  node_count: number;
  chapter_count: number;
  edge_count: number;
  archive_count: number;
}

export interface CustomGraphListResp {
  status: string;
  graphs: CustomGraphMeta[];
}

/** build/regenerate 共用响应形状；exists 属正常提示（唯一性铁律），不是错误。 */
export interface CustomBuildResp {
  status: "built" | "exists" | "regenerated" | "not_found" | "error" | string;
  graph?: CustomGraphMeta;
  warnings?: string[];
  message?: string;
}

export interface CustomRollbackResp {
  status: "ok" | "no_archive" | "not_found" | string;
  graph?: CustomGraphMeta;
  message?: string;
}

export interface CustomDeleteResp {
  status: "ok" | "not_found" | string;
}

export interface CatalogStage {
  level: string;
  subjects: string[];
  /** level === "自定义" 时附带图谱元信息 */
  custom?: CustomGraphMeta[];
}

export interface KnowledgeCatalogResp {
  status: string;
  stages: CatalogStage[];
}

export interface KnowledgeTaxonomyGroup {
  id: string;
  textbook_id: string;
  topic_key: string;
  name: string;
  note: string;
  kind: string;
  scope: string;
  status: string;
  file_ids: string[];
  node_prefix: string;
  node_ids?: string[];
  node_count: number;
  chapter_count: number;
  volumes: KnowledgeTaxonomyVolume[];
  graph_policy?: {
    default_max_chapters?: number | null;
    default_max_concepts?: number | null;
    volume_overrides?: Record<string, { max_chapters?: number | null; max_concepts?: number | null }>;
  };
}

export interface KnowledgeTaxonomyVolume {
  file_id: string;
  name: string;
  chapter_count: number;
  /** 节（课/篇目/小节）数；旧图谱无节层时缺省 */
  section_count?: number;
  concept_count: number;
  status: string;
  truncated: boolean;
  error?: string;
  effective_limits?: { max_chapters?: number | null; max_concepts?: number | null };
}

export interface KnowledgeTaxonomySubject {
  name: string;
  groups: KnowledgeTaxonomyGroup[];
}

export interface KnowledgeTaxonomyLevel {
  name: string;
  subjects: KnowledgeTaxonomySubject[];
}

export interface KnowledgeTaxonomyResp {
  status: string;
  levels: KnowledgeTaxonomyLevel[];
  message?: string;
}


// --- M6 记忆 ---

export interface Episode {
  id: string;
  ts: number;
  summary: string;
  event_type: string;
  concept: string;
  subject: string;
  score: number | null;
  emotion: string;
  importance: number;
  scope: string;
}

export interface EpisodesResp {
  status: string;
  episodes: Episode[];
  has_more: boolean;
}

export interface SemanticFact {
  id?: string;
  category: string;
  scope: string;
  fact?: string;
  text?: string;
  confidence?: number;
  evidence?: string[];
  superseded_by?: string | null;
  created_at?: number;
  [key: string]: unknown;
}

export interface SemanticResp {
  status: string;
  facts: SemanticFact[];
}

export interface ProceduralStrategy {
  strategy: string;
  subject: string;
  scope: string;
  success_rate: number;
  trials: number;
  last_used_ts: number;
}

export interface ProceduralResp {
  status: string;
  strategies: ProceduralStrategy[];
}

/** 跨会话「最近习题」条目（测评中心列表，每学生上限 100 道）。 */
/** 跨会话最近习题（G4：learning-evidence journal 投影，ISO 时间戳）。 */
export interface RecentQuizQuestion {
  /** attempt_id（服务端身份，单次提交键）。 */
  id: string;
  ts: string;
  session_id: string;
  question_id: string;
  question_revision: number;
  topic: string;
  knowledge_point: string;
  type: string;
  stem: string;
  verdict: string;
  student_answer?: string;
  evaluation_status?: string;
  availability?: string;
}

export interface RecentQuizResp {
  status: string;
  questions: RecentQuizQuestion[];
}

/** 错题本条目（G4：learning-evidence journal 投影，新→旧）。 */
export interface ErrorNotebookItem {
  /** 证据来源（打开详情：原题/你的作答/答案解析）。 */
  source_id: string;
  /** 任务族（topic）。 */
  topic: string;
  /** 题目徽标概念（source_badge）。 */
  knowledge_point: string;
  stem: string;
  /** wrong | partial */
  verdict: string;
  /** ISO 时间戳。 */
  ts: string;
}

export interface ErrorNotebookResp {
  status: string;
  items: ErrorNotebookItem[];
  count: number;
}

// --- 统一学习评价（/learner-evaluation/*，plan §11.2/§11.3） ---
// 时间戳均为服务端 ISO 字符串；类别不是等级阶梯，禁止映射数值。

export interface EvalConceptRef {
  graph_owner_namespace: string;
  textbook_id: string;
  file_ids: string[];
  concept_id: string;
  concept_revision: string;
  display_name: string;
  /** 服务端稳定编码（ConceptRef.key，sha256 截断）：概念详情/证据过滤用。 */
  key: string;
}

export interface EvalClaim {
  claim_id: string;
  concept_ref: EvalConceptRef;
  statement: string;
  /** supported | tentative | challenged | unobserved */
  status: string;
  support_refs: string[];
  challenge_refs: string[];
  assistance_scope: string;
  limits: string[];
  created_by_observation: string;
  updated_at_observation: string;
}

export interface EvalLearningChange {
  /** strengthened | weakened | mixed | stable | unknown */
  direction: string;
  /** comparable | partially_comparable | not_comparable | no_prior */
  comparison: string;
  prior_refs: string[];
  current_refs: string[];
  statement: string;
  alternative_explanations: string[];
}

export interface EvalNextProbe {
  /** explain | practice | variant | transfer | delayed_recheck | self_check */
  kind: string;
  concept_ref: string;
  target_claim: string;
  instruction: string;
  rationale: string;
  expected_observation: string;
  /** full_demo | key_hints | independent */
  assistance: string;
  stop_condition: string;
}

export interface ConceptEvaluationView {
  concept_ref: EvalConceptRef;
  /** not_observed | emerging | supported_in_scope | fragile | conflicting | null */
  state: string | null;
  /** ready | pending | reconciling | unavailable | disabled */
  evaluation_status: string;
  judgment_id: string;
  statement: string;
  claims: EvalClaim[];
  change: EvalLearningChange | null;
  next_probe: EvalNextProbe | null;
  /** current | out_of_scope | source_removed | needs_mapping */
  scope_status: string;
  evidence_count: number;
  last_observed_at: string;
  updated_at: string;
}

export interface EvalCoverageCounts {
  observed_concepts: number;
  not_observed_concepts: number;
  by_state: Record<string, number>;
  reconciling_concepts: number;
}

export interface EvalSynthesisChange {
  statement: string;
  prior_refs: string[];
  current_refs: string[];
  comparison: string;
}

export interface EvalOpenQuestion {
  statement: string;
  claim_refs: string[];
}

export interface EvalThemeSummary {
  title: string;
  statement: string;
  claim_refs: string[];
}

export interface EvalScopeSynthesis {
  synthesis_id: string;
  /** concept | session | workspace */
  scope_type: string;
  workspace_id: string;
  statement: string;
  claim_refs: string[];
  theme_summaries: EvalThemeSummary[];
  changes: EvalSynthesisChange[];
  open_questions: EvalOpenQuestion[];
  priority_probe: EvalNextProbe | null;
  limits: string[];
  scope_revision: string;
  evidence_watermark: string;
  pending_source_count: number;
  generated_at: string;
}

export interface WorkspaceEvaluationSummary {
  workspace_id: string;
  scope_revision: string;
  evaluation_status: string;
  evaluated_through: string;
  pending_source_count: number;
  allowed_concept_count: number;
  coverage: EvalCoverageCounts;
  synthesis: EvalScopeSynthesis | null;
  workspace_name: string;
  updated_at: string;
}

export interface WorkspaceEvaluationListItem {
  workspace_id: string;
  workspace_name: string;
  evaluation_status: string;
  scope_revision: string;
  coverage: EvalCoverageCounts;
  updated_at: string;
}

export interface WorkspacesEvaluationResp {
  items: WorkspaceEvaluationListItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface EvalConceptsResp {
  items: ConceptEvaluationView[];
  total: number;
  offset: number;
  limit: number;
  revision: string;
}

export interface EvalSessionItem {
  source_session_ref: string;
  has_evidence: boolean;
  /** available | archived | deleted */
  availability: string;
  last_observed_at: string;
  kinds: string[];
}

export interface EvalSessionsResp {
  items: EvalSessionItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface EvalSessionEvidenceItem {
  source_id: string;
  /** dialogue | assessment */
  kind: string;
  observed_at: string;
  availability: string;
  canonical_text: string;
  interpretation_id: string;
  review_status: string;
}

export interface EvalSourceTimelineItem {
  source_id: string;
  kind: string;
  observed_at: string;
  scope_status: string;
  /** available | archived | deleted */
  availability: string;
  concept_refs: string[];
  summary: string;
  source_session_ref: string;
  interpretation_id: string;
  review_status: string;
}

export interface EvalTimelineResp {
  items: EvalSourceTimelineItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface EvalAssistanceEvent {
  /** hint_requested | answer_revealed | worked_example | teacher_probe | prior_exposure */
  kind: string;
  at: string;
  detail: string;
  client_entry: string;
}

export interface EvalEvidenceTaskPublic {
  illustration?: QuestionIllustrationData | null;
  question_id: string;
  question_revision: number;
  q_type: string;
  stem: string;
  options: Record<string, string>;
  input_spec: { kind: string; max_bytes: number; requires_explanation: boolean };
  concept_refs: Array<Record<string, unknown>>;
  source_badge: string;
  hints_available: boolean;
}

export interface EvalEvidenceDetail {
  source_id: string;
  kind: string;
  observed_at: string;
  /** 当前来源版本（C9 复核 expected_revision / 删除 If-Match 用）。 */
  source_revision?: number;
  /** 当前解释 id（空 = 尚未评价）。 */
  interpretation_id?: string;
  workspace_id: string;
  availability: string;
  canonical_text: string;
  assistance: EvalAssistanceEvent[];
  task: EvalEvidenceTaskPublic | null;
  revealed: { answer: string; explanation: string } | null;
  interpretation: {
    applicable?: boolean;
    abstain_reason?: string | null;
    observation_claims?: Array<Record<string, unknown>>;
    feedback?: string;
    assistance_interpretation?: string;
    next_probe?: EvalNextProbe | null;
  } | null;
  task_result: {
    verdict?: string | null;
    task_score?: number | null;
    grading_status?: string;
    criterion_results?: Array<{ criterion_id: string; result: string; comment: string }>;
    first_error?: { description: string } | null;
    feedback?: { strengths: string[]; improvement: string; next_step: string } | null;
  } | null;
  reviews: Array<{
    review_id: string;
    interpretation_id: string;
    reason: string;
    issue_kind: string;
    requested_at: string;
    status?: string;
    /** R24：真实决定（uphold/revise/invalidate/insufficient_evidence）；
     * 空串 = 仍在复核。 */
    decided_kind?: string;
    decided_at?: string;
    resolution_note?: string;
  }>;
}

export interface EvalJobDetail {
  job_id: string;
  /** queued | running | retry_wait | completed | abstained | failed | cancelled */
  state: string;
  kind: string;
  workspace_id: string;
  error_code: string;
  attempt_count: number;
  transport_attempts: number;
  retryable: boolean;
}

// --- 使用文档（/docs：全员读、管理员写） ---

export interface DocsContentResp {
  status: string;
  markdown: string;
  updated_at: number;
  updated_by: string;
}

// --- M7 评估（既有端点的类型补全） ---

export interface EvalReport {
  ts: number;
  total_turns: number;
  total_evaluated: number;
  failure_distribution: Record<string, number>;
  top_strategies: { strategy: string; subject: string; avg_success_rate: number; sample_size: number }[];
  pending_proposals: number;
  tokens_per_turn?: number | null;
}

export interface EvalProposal {
  id: string;
  ts: number;
  /** 开放式教学指导（现行格式；旧式提案这些字段为空，回落 target/change 展示） */
  title?: string;
  applicability?: string;
  guidance?: string;
  cautions?: string[];
  applied_ts?: number;
  /** 已应用提案的影响回显：应用后落地的教学轮数；null = 旧数据无从统计 */
  impact_turns?: number | null;
  /** 旧式字段（新提案 target 为空，change 镜像 title） */
  target: string;
  change: string;
  rationale: string;
  confidence: number;
  evidence: string[];
  status: "proposed" | "approved" | "applied" | "rejected";
}

/** 生效中/已吊销的教学指导（M7 提案应用后进入 M3，可随时吊销回滚）。 */
export interface EvalGuidanceEntry {
  id: string;
  source_proposal: string;
  title: string;
  applicability: string;
  guidance: string;
  cautions: string[];
  confidence: number;
  applied_at: number;
  active: boolean;
  revoked_at: number;
  impact_turns?: number | null;
}

export interface EvalTrace {
  id: string;
  ts: number;
  session_id: string;
  student_id: string;
  concept: string;
  subject: string;
  intent: string;
  grade: string;
  mode: string;
  outcome: string;
  tool_count: number;
  steps?: number;
  tokens_used: number;
  duration_sec: number;
  failure_type: string;
  failure_cause?: string;
  recommendation?: string;
}

export interface ContextBudgetReport {
  status: string;
  trace_count: number;
  llm_calls: number;
  profile: {
    context_window: number; max_output_tokens: number; safety_margin: number;
    provider: string; llm_runtime_mode: string; tool_message_mode: string;
    tool_projection_mode: string; latest_capabilities?: Record<string, unknown>;
  };
  usage: {
    avg_prompt_tokens: number; avg_completion_tokens: number; avg_total_tokens: number;
    avg_estimated_input_tokens: number; avg_tool_schema_tokens: number;
    avg_reasoning_channel_tokens: number; avg_answer_channel_tokens: number;
  };
  pressure: Record<string, number>;
  reasoning_modes: Record<string, number>;
  compaction: { count: number; estimated_saved_tokens: number };
  tool_projection: {
    samples: number; original_tokens: number; projected_tokens: number;
    estimated_saved_tokens: number; saved_ratio: number;
  };
  recovery: { count: number; provider_or_protocol_fallbacks: number; empty_answer_calls?: number };
}

// --- M4 CAT 自适应测评（既有端点的类型补全） ---

/** QuestionPublic（A07 白名单投影；答案不在答前公开）。 */
export interface AssessmentQuestion {
  illustration?: QuestionIllustrationData | null;
  question_id: string;
  question_revision: number;
  q_type?: string;
  type?: string;
  stem: string;
  options?: Record<string, string>;
  input_spec?: { kind?: string; max_bytes?: number; requires_explanation?: boolean };
  concept_refs?: Array<Record<string, unknown>>;
  source_badge?: string;
  hints_available?: boolean;
  answer?: string;
  explanation?: string;
  knowledge_point?: string;
  difficulty?: string | number;
  grounding_mode?: string;
  grounding_tier?: string;
  source_refs?: import("./types").QuizSourceRef[];
  [key: string]: unknown;
}

export interface AssessmentStartResp {
  status: string;
  session_id?: string;
  /** W2/A03：本次 CAT 的独立 id（session_id 字段历史上回显的是学生 id）。 */
  assessment_id?: string;
  difficulty?: number;
  question?: AssessmentQuestion;
  message?: string;
  /** strict 教材测评未找到可靠证据时（status=grounding_not_found）的元数据 */
  grounding?: {
    mode?: string;
    tier?: string;
    required?: boolean;
    reason?: string;
    query?: string;
    source_count?: number;
  };
}

/** /assessment/answer（§11.5）：task_result + evaluation 两层。 */
export interface AssessmentAnswerResp {
  status: string;
  assessment_id?: string;
  task_result?: {
    verdict?: string | null;
    grading_status?: string;
    task_score?: number | null;
    criterion_results?: Array<{ criterion_id: string; result: string; comment?: string }>;
    first_error?: { description?: string } | null;
    hypotheses?: Array<{ statement?: string }>;
    feedback?: { strengths?: string[]; improvement?: string; next_step?: string } | null;
  } | null;
  evaluation?: { status: string; interpretation_id: string };
  stop_reason?: string | null;
  summary?: AssessmentSummary;
}

export interface AssessmentNextResp {
  status: string;
  assessment_id?: string;
  question?: AssessmentQuestion | null;
  difficulty?: number;
  stop_reason?: string | null;
  summary?: AssessmentSummary;
}

/** GET /assessment/active：刷新/重开/双标签共用的恢复视图。 */
export interface AssessmentActiveResp {
  /** ok（有会话）| none（无会话）| disabled | error */
  status: string;
  assessment_id?: string;
  /** active | mastered | stopped | abandoned */
  session_status?: string;
  answered?: number;
  stop_reason?: string | null;
  /** 待答时为当前题公开内容（不含答案/解析），否则为 null。 */
  question?: AssessmentQuestion | null;
  summary?: AssessmentSummary;
}

/** cat.report（§11.5）：本次表现 + 语义总结；分清 pending 与题目局部结果。 */
export interface AssessmentSummary {
  assessment_id?: string;
  workspace_id?: string;
  status?: string;
  /** P3/硬上限枚举：sufficient_for_current_claim|needs_clarification|max_questions|max_time|user_stopped|generation_failed */
  stop_code?: string;
  /** 判分器给出的自由文本结束说明（可空）。 */
  stop_reason?: string | null;
  asked?: number;
  graded?: number;
  pending?: number;
  counts?: { correct?: number; partial?: number; wrong?: number };
  difficulty?: number;
  items?: Array<{
    question?: AssessmentQuestion | null;
    question_id: string;
    attempt_id: string;
    observed_at: string;
    task_result?: {
      verdict?: string | null;
      grading_status?: string;
      feedback?: { strengths?: string[]; improvement?: string; next_step?: string } | null;
      first_error?: { description?: string } | null;
    } | null;
    evaluation_status?: string;
    feedback?: string;
  }>;
  [key: string]: unknown;
}

// --- M0 账户资料（/user/profile，需登录） ---

export interface UserProfileData {
  name: string;
  grade: string;
  school: string;
  subjects: string[];
  avatar: string;
  /** 通用每用户偏好（ocr_parallel OCR 并行、tts_speed 朗读语速）。 */
  prefs?: {
    ocr_parallel?: boolean;
    tts_speed?: number;
    quiz_svg_enabled?: boolean;
    quiz_critic_enabled?: boolean;
    quiz_illustration_review_enabled?: boolean;
  };
}

// --- M9 学习编排（/orchestration/* · 无 status 信封，空态靠空字段/空数组表达） ---
// 时间戳均为 unix float 秒；0 = 未设置。

export interface OrchGoal {
  /** 稳定 id（g_{n}）：多目标下 API 寻址用。 */
  id?: string;
  title: string;
  description: string;
  /** exam | ability | interest */
  goal_type: string;
  subjects: string[];
  /** 概念级绑定（L1 目标链）：绑定的图谱概念 id */
  target_concept_ids?: string[];
  /** G4 §13.8：目标的工作区归属（评价/计划按区隔离）。 */
  workspace_id?: string;
  deadline: number;
  created_at: number;
  updated_at: number;
}

export interface OrchGap {
  skill_id: string;
  name: string;
  subject: string;
  difficulty: number;
  /** unknown=未观察（不宣称缺口） | weak=已观察待解决 | missing=旧数据 */
  status: string;
  /** 拓扑层级：1 = 现在就能学；0 = 未分层（旧数据） */
  layer?: number;
}

/** goal_state.estimate：确定性排期预估（纯函数零 LLM）。 */
export interface OrchGoalEstimate {
  weekly_pace: number;
  est_weeks: number;
  /** W4/A13 时间容量区间（daily_minutes×可用天 折算的快/慢节奏上下界）。 */
  est_weeks_min?: number;
  est_weeks_max?: number;
  weekly_capacity_minutes?: number;
  time_pace?: number;
  weeks_left: number | null;
  fit: "tight" | "ok" | "loose" | "none" | string;
  required_count: number;
}

export interface OrchGoalState {
  /** 与 goals[].id 配对（多目标）。 */
  goal_id?: string;
  goal_title: string;
  goal_type: string;
  subject: string;
  deadline: number;
  /** 已支持概念占比（0..1，语义口径：supported_in_scope / 范围内概念） */
  supported_ratio: number;
  total_skills: number;
  supported_skills: number;
  gaps: OrchGap[];
  required_skills: string[];
  recommended_strategy: string;
  urgency: number;
  analyzed_at: number;
  /** concept_chain = 目标链口径（绑定概念的前置闭包）；subject = 全学科口径 */
  chain_mode?: "concept_chain" | "subject" | string;
  target_concept_ids?: string[];
  estimate?: OrchGoalEstimate;
}

export interface OrchMilestone {
  id: string;
  title: string;
  concept_ids: string[];
  /** not_started | in_progress | completed */
  status: string;
  order: number;
}

export interface OrchWeeklyConcept {
  concept_id: string;
  name: string;
  milestone_id: string;
  week_index: number;
  difficulty: number;
}

/** 周任务内的一个可执行子任务（LLM 推荐或手动添加）。 */
export interface OrchSubTask {
  id: string;
  title: string;
  /** auto=规划生成（重规划会重建） | user=手动添加（永不覆盖） */
  source: string;
  estimate_minutes: number;
  done: boolean;
  done_at: number;
}

/** 周计划内的一个行动级任务。 */
export interface OrchWeekTask {
  id: string;
  title: string;
  concept_ids: string[];
  kind: string;
  source: string;
  done: boolean;
  subtasks: OrchSubTask[];
  /** G4 §13.8：继承 goal 的工作区归属。 */
  workspace_id?: string;
}

export interface OrchWeek {
  week_index: number;
  week_start: number;
  focus: string;
  /** auto=系统生成 | user=手动添加（重规划时整周保留） */
  origin: string;
  concepts: OrchWeeklyConcept[];
  tasks: OrchWeekTask[];
}

export interface OrchDailyTask {
  id: string;
  /** YYYY-MM-DD */
  day: string;
  concept_id: string;
  concept_name: string;
  /** study | review | practice | summary */
  kind: string;
  /** pending | in_progress | completed | skipped | overdue */
  status: string;
  priority: number;
  estimate_minutes: number;
  milestone_id: string;
  /** 物化来源：周任务 / 子任务（完成时回写子任务）。 */
  week_task_id: string;
  subtask_id: string;
  created_at: number;
  completed_at: number;
  /** 自定义标题；为空时前端渲染 concept_name。 */
  title: string;
  /** "" | foundation | reinforce | sprint */
  phase: string;
  /** 用户自建任务（任何管线不碰）。 */
  custom: boolean;
  /** 教练批注「为什么今天学这个」，可为空。 */
  reason: string;
  /** W4/A12 launch 绑定：任务关联的 episode 与预创建会话。 */
  episode_id?: string;
  session_id?: string;
  /** "" | quiz_evidence（绑定作答完成） | self_report（手动勾选）。 */
  completion_source?: string;
  /** quiz_evidence 完成时的作答 attempt 引用。 */
  evidence_attempt_id?: string;
}

/** POST /orchestration/task/{id}/launch 的响应（W4/A12 任务启动绑定）。 */
export interface OrchLaunchResp {
  ok: boolean;
  task_id: string;
  episode_id: string;
  session_id: string;
  /** 服务端预创建会话的跳转地址（/chat/<sid>）；前端追加 ?q=&send=1。 */
  launch_url: string;
  /** true = 复用未完成任务的既有 episode/会话（幂等 relaunch）。 */
  resumed: boolean;
}

/** POST /orchestration/goal 的响应（新增一个目标后自动重规划 + kickoff）。 */
export interface OrchGoalResp {
  ok: boolean;
  /** 新建目标的 id（POST /goal 返回）。 */
  goal_id?: string;
  weeks: OrchWeek[];
  first_task?: OrchDailyTask | null;
  /** regenerate 结果细分："" 正常 | "no_goal" 未设目标 | "empty_plan" 暂无可安排内容（合法终态，非错误）。 */
  reason?: "" | "no_goal" | "empty_plan" | string;
}

/** POST /orchestration/task 的响应。 */
export interface OrchTaskResp {
  ok: boolean;
  task: OrchDailyTask;
}

/** POST /orchestration/week 的响应。 */
export interface OrchWeekResp {
  ok: boolean;
  week: OrchWeek;
}

/** POST /orchestration/week/{i}/concept 的响应。 */
export interface OrchWeekConceptResp {
  ok: boolean;
  concept: OrchWeeklyConcept;
}

export interface OrchHabit {
  current_streak: number;
  longest_streak: number;
  last_active_day: string;
  total_active_days: number;
  completed_tasks: number;
  total_tasks: number;
  procrastination_count: number;
  completion_rate: number;
  updated_at: number;
}

export interface OrchReviewItem {
  concept_id: string;
  concept_name: string;
  /** G4 §13.8：卡片键 (workspace_id, concept_key) 的工作区侧。 */
  workspace_id?: string;
  easiness: number;
  /** 天 */
  interval: number;
  repetitions: number;
  next_review: number;
  /** null = 尚无真实召回观测（W4/A07：新卡不再伪装成 pass-3） */
  last_quality: number | null;
  created_at: number;
}

export interface OrchSchedule {
  daily_minutes: number;
  available_days: string[];
  preferred_time: string;
  exam_dates: Record<string, number>;
}

/** W4 容量可行性：确定性按日负载 vs 时间预算（advisory）。 */
export interface OrchCapacityDay {
  /** YYYY-MM-DD */
  day: string;
  planned_minutes: number;
  tasks: number;
  overload: boolean;
}

export interface OrchCapacity {
  daily_minutes: number;
  days: OrchCapacityDay[];
  overload_days: string[];
}

/** GET /orchestration/plan — 异常兜底时 goals/goal_states/schedule/habit 退化为空。 */
export interface OrchPlanSummary {
  student_id: string;
  /** 多个长期目标（上限 4），与 goal_states 按 goal_id 一一配对。 */
  goals: Partial<OrchGoal>[];
  goal_states: Partial<OrchGoalState>[];
  /** 旧里程碑数据（兼容字段，新架构不再产出）。 */
  milestones: OrchMilestone[];
  weekly_plan: OrchWeek[];
  daily_tasks: OrchDailyTask[];
  schedule: Partial<OrchSchedule>;
  habit: Partial<OrchHabit>;
  review_queue: Record<string, OrchReviewItem>;
  srs_due_count: number;
  pending_today: number;
  /** 进度与计划出现偏差，建议重新规划（前端 banner 引导）。 */
  needs_replan: boolean;
  /** W4：按日负载报告（读侧聚合，缺失时视为无超载）。 */
  capacity?: OrchCapacity;
  event_count: number;
  events_processed: number;
  created_at: number;
  updated_at: number;
}
