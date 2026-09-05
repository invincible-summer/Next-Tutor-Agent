// M2/M3/M5/M6 只读投影端点的响应类型（后端 api/v1/{student,knowledge,memory}.py）。

export interface StudentProfileData {
  id: string;
  grade: string;
  subjects: string[];
  learning_style: { preference: string; explanation_depth: string };
  goals: string[];
  weak_points: string[];
  strong_points: string[];
  created_at: number;
  updated_at: number;
  last_active: number;
  events_processed: number;
}

export interface StudentProfileResp {
  status: string;
  profile: StudentProfileData | null;
}

export type ConceptState = "understood" | "partial" | "misconception" | "introduced" | string;

export interface MasterySkill {
  skill_id: string;
  concept: string;
  subject: string;
  p_known: number;
  state: ConceptState;
  attempts: number;
  correct: number;
  last_review: number;
  mistakes: string[];
}

export interface MasteryResp {
  status: string;
  skills: MasterySkill[];
  count: number;
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
  review: PathNode[];
  difficulty?: number | null;
}

// --- M5 知识图谱 ---

export interface KnowledgeNodeMastery {
  p_known: number;
  state: ConceptState;
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
  mastery: KnowledgeNodeMastery | null;
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
  mastery: (KnowledgeNodeMastery & { attempts?: number; correct?: number }) | null;
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
export interface RecentQuizQuestion {
  id: number;
  ts: number;
  session_id: string;
  topic: string;
  grade: string;
  type: string;
  difficulty: string;
  stem: string;
  verdict: string;
  student_answer?: string;
  source_status?: "active" | "deleted" | "independent";
  source_message?: string;
}

export interface RecentQuizResp {
  status: string;
  questions: RecentQuizQuestion[];
}

/** 错题本条目（跨会话聚合 verdict=wrong/partial 的题）。 */
export interface ErrorNotebookItem {
  session_id: string;
  source_session_id?: string;
  source_kind?: "chat" | "assessment";
  source_status?: "active" | "deleted" | "independent";
  source_message?: string;
  session_title: string;
  topic: string;
  knowledge_point: string;
  stem: string;
  type: string;
  difficulty: string;
  verdict: string;
  student_answer: string;
  correct_answer: string;
  explanation: string;
  ts: number;
}

export interface ErrorNotebookResp {
  status: string;
  items: ErrorNotebookItem[];
  count: number;
}

/** 学习账本条目（L1 档案层：独立于对话的学习结果全量记录）。 */
export interface LearningRecordItem {
  record_id: string;
  session_id: string;
  source_kind: "chat" | "assessment" | string;
  source_status: "active" | "deleted" | "independent" | string;
  knowledge_point: string;
  subject: string;
  stem: string;
  type: string;
  difficulty: string;
  student_answer: string;
  verdict: string;
  score: number | null;
  created_at: number;
  updated_at: number;
}

export interface LearningRecordsResp {
  status: string;
  items: LearningRecordItem[];
  count: number;
  total: number;
  offset: number;
  limit: number;
}

/** 布鲁姆认知档案（/student/bloom-profile，学习账本确定性聚合）。 */
export interface BloomLevelStat {
  attempts: number;
  correct: number;
  rate: number;
}

export interface BloomWeakness {
  concept: string;
  level: string;
  level_zh: string;
  attempts: number;
  rate: number;
}

export interface BloomProfileResp {
  status: string;
  concepts: Record<string, { levels: Record<string, BloomLevelStat>; last_at: number }>;
  overall: Record<string, BloomLevelStat>;
  weaknesses: BloomWeakness[];
  totals: { records: number; tagged: number };
  updated_at: number;
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
  avg_learning_gain: number | null;
  failure_distribution: Record<string, number>;
  top_strategies: { strategy: string; subject: string; avg_gain: number; avg_success_rate: number; sample_size: number }[];
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
  tokens_used: number;
  duration_sec: number;
  before_mastery: number | null;
  after_mastery: number | null;
  learning_gain: number | null;
  failure_type: string;
  failure_cause?: string;
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

export interface AssessmentQuestion {
  stem: string;
  q_type?: string;
  type?: string;
  options?: Record<string, string>;
  answer?: string;
  explanation?: string;
  knowledge_point?: string;
  difficulty?: string | number;
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
}

export interface AssessmentAnswerResp {
  status: string;
  assessment_id?: string;
  result?: { verdict?: string; score?: number; feedback?: string; [key: string]: unknown };
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

export interface AssessmentSummary {
  verdict?: string;
  asked?: number;
  correct?: number;
  concept?: string;
  recommendation?: string;
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
  prefs?: { ocr_parallel?: boolean; tts_speed?: number };
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
  deadline: number;
  created_at: number;
  updated_at: number;
}

export interface OrchGap {
  skill_id: string;
  name: string;
  subject: string;
  difficulty: number;
  /** missing | weak */
  status: string;
  current_mastery: number;
  target_mastery: number;
  /** 拓扑层级：1 = 现在就能学；0 = 未分层（旧数据） */
  layer?: number;
}

/** goal_state.estimate：确定性排期预估（纯函数零 LLM）。 */
export interface OrchGoalEstimate {
  weekly_pace: number;
  est_weeks: number;
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
  /** novice | beginner | intermediate | advanced | proficient */
  current_level: string;
  target_level: string;
  mastered_ratio: number;
  total_skills: number;
  mastered_skills: number;
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
  target_mastery: number;
}

export interface OrchWeeklyConcept {
  concept_id: string;
  name: string;
  milestone_id: string;
  week_index: number;
  difficulty: number;
  planned_mastery: number;
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
  easiness: number;
  /** 天 */
  interval: number;
  repetitions: number;
  next_review: number;
  last_quality: number;
  created_at: number;
}

export interface OrchSchedule {
  daily_minutes: number;
  available_days: string[];
  preferred_time: string;
  exam_dates: Record<string, number>;
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
  event_count: number;
  events_processed: number;
  created_at: number;
  updated_at: number;
}
