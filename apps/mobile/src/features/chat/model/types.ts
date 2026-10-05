/**
 * Chat 域视图模型：对齐 apps/web/src/lib/types.ts 的聊天子集。
 * 服务端契约字段（如 grade 的中文枚举）保持原值不翻译。
 * exactOptionalPropertyTypes：可选字段一律声明 `| undefined`，允许显式传 undefined。
 */

export {
  AUTO_GRADE,
  GRADES,
  gradeForApi,
  gradeFromApi,
  type Grade,
} from "@/lib/grade";

export interface AttachmentMeta {
  id: string;
  filename: string;
  char_count: number;
  chunk_count?: number | undefined;
  error?: string | undefined;
  folder_id?: string | undefined;
  has_original?: boolean | undefined;
  warning?: string | undefined;
  ocr_used?: boolean | undefined;
  preview_text?: string | undefined;
  source_scope?: string | undefined;
  source_visibility?: string | undefined;
  library_file_id?: string | undefined;
}

export interface ToolResultData {
  tool: string;
  status: "success" | "partial" | "error";
  data: Record<string, unknown>;
  text: string;
  error?: { code: string; message: string } | null | undefined;
  error_code?: string | null | undefined;
}

export interface ToolCallRecord {
  name: string;
  result?: ToolResultData | undefined;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  message_id?: string | undefined;
  thinking?: string | undefined;
  toolCalls?: ToolCallRecord[] | undefined;
  attachments?: AttachmentMeta[] | undefined;
}

export interface SessionItem {
  session_id: string;
  workspace_id?: string | undefined;
  grade: string;
  title: string;
  message_count: number;
  round_count?: number | undefined;
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
  updated_at: number;
}

/** loadSession 响应（tail 渐进加载）。 */
export interface ChatSessionDetail {
  messages?: ChatMessage[] | undefined;
  knowledge_files?: AttachmentMeta[] | undefined;
  material_sources?: AttachmentMeta[] | undefined;
  grade?: string | undefined;
  workspace_id?: string | undefined;
  message_total?: number | undefined;
}

export interface RetryState {
  attempt: number;
  reason: string;
  visible: boolean;
}

// --- 服务端身份题卡（对齐 Web QuizQuestion 子集） -----------------------------

export interface QuizSourceRef {
  chunk_id?: string | undefined;
  file_id?: string | undefined;
  filename?: string | undefined;
  section_path?: string[] | undefined;
  printed_page?: number | null | undefined;
  page?: number | null | undefined;
  excerpt?: string | undefined;
}

export interface QuizQuestionResult {
  verdict: string | null;
  student_answer: string;
  attempt_id?: string | undefined;
  evaluation?:
    { status: string; interpretation_id?: string | undefined } | undefined;
}

export interface QuizQuestion {
  id: number;
  type: "multiple_choice" | "fill_blank" | "short_answer";
  stem: string;
  options?: Record<string, string> | undefined;
  answer: string;
  explanation: string;
  knowledge_point?: string | undefined;
  difficulty?: string | undefined;
  question_id?: string | undefined;
  question_revision?: number | undefined;
  result?: QuizQuestionResult | undefined;
  grounding_mode?: string | undefined;
  grounding_tier?: string | undefined;
  source_refs?: QuizSourceRef[] | undefined;
  /** 配图（M3 题图渲染前先展示占位；visual_role=essential 且无图时禁止提交）。 */
  illustration?: unknown;
  visual_role?: "none" | "supplemental" | "essential" | undefined;
}
