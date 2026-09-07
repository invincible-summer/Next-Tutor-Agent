export type Grade = "自动" | "小学" | "初中" | "高中" | "本科";

/** P1 学段去僵化：前端 UI 用「自动」token，后端事实源是空串。
 * 发往后端（chatStream/upload/attach/PATCH）前用 gradeForApi 转成 ""；
 * 从后端读回（session.grade）用 gradeFromApi 转成「自动」展示。 */
export const AUTO_GRADE = "自动";

export function gradeForApi(g: Grade | string | undefined | null): string {
  return g === AUTO_GRADE ? "" : (g ?? "");
}

export function gradeFromApi(g: string | undefined | null): Grade {
  if (!g) return AUTO_GRADE;
  return g as Grade;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  toolCalls?: ToolCallRecord[];
  attachments?: AttachmentMeta[];
}

export interface ToolCallRecord {
  name: string;
  result: ToolResultData;
}

export interface ToolResultData {
  tool: string;
  status: "success" | "partial" | "error";
  data: Record<string, unknown>;
  text: string;
  error?: { code: string; message: string } | null;
  error_code?: string | null;
}

export interface AttachmentMeta {
  id: string;
  filename: string;
  char_count: number;
  chunk_count?: number;
  error?: string;
  /** 资料库文件所属文件夹（工作区知识文件来自 library 时携带） */
  folder_id?: string;
  /** 后端是否保留原件：无原件的旧文件不提供下载（按钮隐藏） */
  has_original?: boolean;
  warning?: string;
  ocr_used?: boolean;
  ocr_pages?: number[];
  media_count?: number;
  preview_text?: string;
  source_scope?: string;
  source_visibility?: string;
  library_file_id?: string;
}

export interface SessionItem {
  session_id: string;
  workspace_id?: string;
  grade: string;
  title: string;
  message_count: number;
  /** 轮数：一次 agent 回复算一轮（缺省回退 message_count） */
  round_count?: number;
  quiz_count: number;
  file_count: number;
  updated_at: number;
}

export interface WorkspaceItem {
  workspace_id: string;
  name: string;
  session_count: number;
  file_count: number;
  has_memory: boolean;
  library_folder_id?: string;
  selected_folder_ids?: string[];
  selected_file_ids?: string[];
  workspace_file_ids?: string[];
  updated_at: number;
}

export interface WorkspaceDetail {
  workspace_id: string;
  name: string;
  session_ids: string[];
  knowledge_files: AttachmentMeta[];
  library_folder_id?: string;
  selected_folder_ids?: string[];
  selected_file_ids?: string[];
  workspace_file_ids?: string[];
  public_memory: string;
  public_memory_updated_at: number;
  created_at: number;
  updated_at: number;
}

// --- M7 资料库（Library）：文件夹 + 文件两级，按用户隔离 ---

export interface LibraryFolder {
  id: string;
  name: string;
  /** 非空 = 工作区专属资料夹（随工作区管理，不可单独改名/删除） */
  workspace_id: string;
  file_count: number;
  created_at: number;
  updated_at: number;
}

export interface LibraryFile {
  id: string;
  filename: string;
  original_filename?: string;
  /** "" = 未归档（根） */
  folder_id: string;
  char_count: number;
  chunk_count?: number;
  summary?: string;
  topics?: string[];
  /** 是否保留原始文件（老文件只保留提取文本，下载降级为 .txt） */
  has_original: boolean;
  created_at: number;
  updated_at?: number;
}

export interface LibraryTree {
  folders: LibraryFolder[];
  files: LibraryFile[];
}

export interface SessionDetail {
  session_id: string;
  workspace_id?: string;
  grade: string;
  title: string;
  messages: ChatMessage[];
  /** P3 渐进加载：?tail=N 请求时为全量消息总数（缺省=全量，等于 messages.length）。 */
  message_total?: number;
  quiz_history: Record<string, unknown>[];
  knowledge_files: AttachmentMeta[];
  knowledge_summary: { files: AttachmentMeta[]; total_chunks: number };
  material_sources?: MaterialSource[];
  trace_ids: string[];
}

export interface MaterialSource extends AttachmentMeta {
  source_scope?: "session" | "library" | "workspace" | "workspace_textbook" | string;
  source_visibility?: "session_private" | "workspace_shared" | "public" | "private" | string;
  library_file_id?: string;
}

export interface ChatSSEEvent {
  type: string;
  [key: string]: unknown;
}

export interface QuizQuestion {
  id: number;
  type: "multiple_choice" | "fill_blank" | "short_answer";
  stem: string;
  options?: Record<string, string>;
  answer: string;
  explanation: string;
  knowledge_point: string;
  difficulty: string;
  /** 已作答记录（后端写回；存在时答题卡恢复为已答锁定状态） */
  result?: { verdict: string; student_answer: string };
}

export interface RetryState {
  attempt: number;
  reason: string;
  visible: boolean;
}

// --- M8 UX Intelligence ---

export interface UxInteractionStyle {
  tone: string;          // encouraging | neutral | formal
  detail_level: string;  // concise | medium | detailed
  visual_preference: boolean;
  pacing: string;        // steady | fast | slow
  patience: string;      // low | medium | high
}

export interface UxProfileSummary {
  student_id: string;
  style: UxInteractionStyle;
  motivation: { last_nudge_ts: number; last_milestone_surfaced: number };
  recent_feedback_counts: Record<string, number>;
  avg_response_length: number;
  abandon_signals: number;
  event_count: number;
  updated_at: number;
}

export interface UxMotivation {
  streak_days: number;
  next_milestone: number | null;
  milestones: number[];
  active_days: number;
}

/** /ux/activity：统一活跃度聚合（作答/讲解/复习三系列 + streak 摘要）。 */
export interface UxActivityDay {
  date: string;
  answers: number;
  teachings: number;
  reviews: number;
}

export interface UxActivity {
  days: UxActivityDay[];
  source: "aggregated" | "legacy_episodes" | "none" | string;
  streak_days: number;
  longest_streak: number;
  last_active_day: string;
  active_days: number;
}

export interface UxGreeting {
  greeting: string;
  lang: string;
}

// --- Unified recycle bin / lifecycle ---
export type TrashResourceType =
  | "session"
  | "library_file"
  | "library_folder"
  | "textbook"
  | "textbook_volume"
  | "workspace"
  | "knowledge_graph";

export interface TrashItem {
  id: string;
  resource_type: TrashResourceType;
  original_id: string;
  title: string;
  deleted_at: number;
  deleted_at_iso: string;
  expires_at: number | null;
  retention_days: number;
  size_bytes?: number;
  /** Project-relative, user-visible recycle-bin location (never an absolute host path). */
  archive_location?: string;
  version: number;
  metadata: {
    workspace_id?: string;
    workspace_ids?: string[];
    file_ids?: string[];
    topic_key?: string;
    session_count?: number;
    file_count?: number;
    round_count?: number;
    has_public_memory?: boolean;
    memory_forget_status?: "recent" | "compacted" | "legacy_unknown" | "none" | "unavailable";
    memory_forget_result?: string;
    [key: string]: unknown;
  };
}

export interface TrashPolicy {
  default_days: number;
  user_max_days: number;
  forced_max_days: number;
  mode: "auto" | "manual";
  cleanup_interval_seconds: number;
  retention_days: number;
  can_keep_manually: boolean;
}

export interface PromptMemoryProfile {
  status: string;
  window_size: number;
  max_window: number;
  core_profile: Record<string, string>;
  recent_sessions: Array<{
    session_id: string;
    workspace_id: string;
    created_at: number;
    updated_at: number;
    has_contribution: boolean;
  }>;
  compacted_session_count: number;
  compacted_attribution_count?: number;
  legacy_compacted_attribution_unknown?: number;
  compaction_generation?: number;
  last_compacted_at?: number;
  directive_chars: number;
}
