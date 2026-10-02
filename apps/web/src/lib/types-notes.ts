// M-Notes 笔记仓库的前端类型。字段与 backend/app/core/notes.py 的
// vault_summary / note_summary / suggestions 投影一一对应。

export interface NotesFolder {
  id: string;
  name: string;
  parent_id: string;
  created_at: number;
  updated_at: number;
  note_count: number;
}

export interface NoteReviewState {
  enabled: boolean;
  next_review_at: number;
  easiness: number;
  interval: number;
  repetitions: number;
}

export interface NoteSource {
  workspace_id?: string;
  session_ids?: string[];
  textbook_ids?: string[];
  use_error_notebook?: boolean;
}

export interface NoteSummary {
  id: string;
  title: string;
  folder_id: string;
  tags: string[];
  template_id: string;
  status: "draft" | "active" | "archived";
  revision: number;
  source: NoteSource;
  review: NoteReviewState;
  created_at: number;
  updated_at: number;
  created_by: string;
  word_count: number;
  links_rewritten?: number;
}

export interface VaultStats {
  note_count: number;
  folder_count: number;
  link_count: number;
  unresolved_links: string[];
  due_review_count: number;
  due_review_ids: string[];
}

export interface VaultSnapshot {
  folders: NotesFolder[];
  notes: NoteSummary[];
  tags: Record<string, number>;
  custom_templates: CustomTemplate[];
  stats: VaultStats;
}

export interface NoteResourceLink {
  type: "note" | "session" | "notes_thread";
  resource_id: string;
  url: string;
  title: string;
  status: "resolved" | "missing" | "deleted";
  resolved: boolean;
  folder_id?: string;
  folder_name?: string;
  message_count?: number;
  updated_at?: number;
}

export interface ResolvedLink {
  title: string;
  note_id: string;
}

export interface NoteDetail {
  note: NoteSummary;
  content: string;
  backlinks: NoteSummary[];
  links: { resolved: ResolvedLink[]; unresolved: string[]; resources?: NoteResourceLink[] };
  inline_tags: string[];
}

export interface NoteRevision {
  revision: number;
  ts: number;
  author: string;
  word_count: number;
}

export interface NoteTemplate {
  id: string;
  name: string;
  name_en?: string;
  description?: string;
  folder_hint?: string;
  suggested_tags?: string[];
  review_enabled?: boolean;
  sources?: string[];
  skeleton?: string;
  content?: string; // 自定义模板正文
  builtin: boolean;
  created_at?: number;
}

export interface CustomTemplate {
  id: string;
  name: string;
  content: string;
  created_at: number;
}

export interface GraphNode {
  id: string;
  title: string;
  folder_id: string;
  tags: string[];
  ghost: boolean;
  kind: "note" | "session" | "notes_thread" | "textbook" | "ghost";
  status?: "resolved" | "unresolved" | "missing" | "deleted";
  resource_id?: string;
  folder_name?: string;
  message_count?: number;
  updated_at?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  title: string;
  resolved: boolean;
  kind: "note" | "session" | "notes_thread" | "textbook" | "unresolved";
  status?: string;
}

export interface NotesGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/**
 * 笔记智能体三模式（2026-09 重构，与后端 AGENT_MODES 一一对应）：
 * ask 仅问答；plan 只产计划卡（不写入）；authorize 直接修改当前笔记。
 * 旧值 suggest/collab→plan、cowrite/auto→authorize（由 normalizeAgentMode 迁移）。
 */
export type AgentMode = "ask" | "plan" | "authorize";

export const AGENT_MODES: AgentMode[] = ["ask", "plan", "authorize"];

/** 旧客户端/本地存储的四模式值 → 新三模式（与后端 _LEGACY_AGENT_MODES 同步）。 */
export function normalizeAgentMode(raw: string | null | undefined): AgentMode {
  const legacy: Record<string, AgentMode> = {
    suggest: "plan", collab: "plan", cowrite: "authorize", auto: "authorize",
  };
  const value = (raw ?? "").trim().toLowerCase();
  const mapped = legacy[value] ?? value;
  return (AGENT_MODES as string[]).includes(mapped) ? mapped as AgentMode : "ask";
}

/** 计划批复状态机：pending 待批复 → approved 批复(执行中) → executed 已执行；rejected 已驳回。 */
export type AgentPlanStatus = "pending" | "approved" | "rejected" | "executed";

export interface AgentPlanStep {
  title: string;
  detail: string;
}

export interface AgentPlan {
  status: AgentPlanStatus;
  title: string;
  steps: AgentPlanStep[];
  /** 产出计划的助手全文（批复后作为 <approved_plan> 注入执行轮） */
  plan_text?: string;
  created_at?: number;
  decided_at?: number;
  executed_at?: number;
}

/** 每笔记专属智能体的一条对话消息（thinking 不落盘，无对应字段）。 */
export interface AgentMessage {
  role: "user" | "assistant";
  content: string;
  context: {
    note_id?: string;
    mode?: string;
    action?: string;
    stopped?: boolean;
    error?: boolean;
    attachments?: { id: string; filename: string }[];
    tools?: { tool: string; status: string; summary: string }[];
  };
  ts: number;
}

/** GET/PATCH /notes/{id}/agent 的视图（note_id 为空 = 仓库级对话，键为 _vault）。 */
export interface AgentHistory {
  note_id: string;
  mode: AgentMode | string;
  messages: AgentMessage[];
  pending_plan: AgentPlan | null;
  working: { stage?: string; tool?: string; started_at?: number; can_stop?: boolean; run_id?: string };
  modes?: string[];
  created_at: number;
  updated_at: number;
}

/** 笔记智能体 SSE 事件（generate 与 chat/stream 共用词汇表）。 */
export interface NotesSSEEvent {
  type: string;
  run_id?: string;
  stage?: string;
  tool?: string;
  status?: string;
  can_stop?: boolean;
  [key: string]: unknown;
}
