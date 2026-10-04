// 助手导航目标 → 站内 URL 的唯一白名单拼装点（A10）。
// 只认契约内 NavigationTarget；未知目标一律返回 null，不得由模型文本
// 构造 URL。A11 会为 memory/orchestration/insights 等扩展深链参数。
import type { NavigationTarget } from "./types.generated";

/** route_id → 站内路径（与 lib/nav.ts 的 NAV 一致）。 */
const MODULE_PATHS: Record<string, string> = {
  home: "/",
  chat: "/chat",
  course: "/course",
  notes: "/notes",
  dashboard: "/dashboard",
  knowledge: "/knowledge",
  orchestration: "/orchestration",
  assessment: "/assessment",
  tools: "/tools",
  tools_illustration: "/tools/illustration",
  memory: "/memory",
  resources_files: "/resources/files",
  resources_textbooks: "/resources/textbooks",
  archive: "/archive",
  profile: "/profile",
  account: "/account",
  settings: "/settings",
  insights: "/insights",
  docs: "/docs",
  admin: "/admin",
  login: "/login",
  register: "/register",
};

/** NavigationTarget → URL；不在白名单内的形状返回 null（不猜测）。 */
export function resolveTargetUrl(target: NavigationTarget | null | undefined): string | null {
  if (!target) return null;
  switch (target.kind) {
    case "module": {
      const path = MODULE_PATHS[target.route_id];
      return path ?? null;
    }
    case "workspace_chat":
      return `/chat?ws=${encodeURIComponent(target.workspace_id)}`;
    case "chat_session":
      return `/chat/${encodeURIComponent(target.session_id)}`;
    case "workspace_courses":
      return `/workspaces/${encodeURIComponent(target.workspace_id)}/classroom`;
    case "lesson": {
      // §8.3：默认课程介绍页；view=edit → ?edit=1；revision=N 固定版本。
      const params = new URLSearchParams();
      if (target.view === "edit") params.set("edit", "1");
      if (target.revision && target.revision >= 1) {
        params.set("revision", String(target.revision));
      }
      const query = params.toString();
      return `/workspaces/${encodeURIComponent(target.workspace_id)}`
        + `/classroom/${encodeURIComponent(target.lesson_id)}`
        + (query ? `?${query}` : "");
    }
    case "classroom_run":
      return `/workspaces/${encodeURIComponent(target.workspace_id)}`
        + `/classroom/${encodeURIComponent(target.lesson_id)}`
        + `/learn/${encodeURIComponent(target.run_id)}`;
    case "note":
      return noteUrl(target.note_id);
    case "learning_archive": {
      const params = new URLSearchParams();
      if (target.workspace_id) params.set("ws", target.workspace_id);
      if (target.tab && target.tab !== "changes") params.set("tab", target.tab);
      if (target.concept_key) params.set("concept", target.concept_key);
      if (target.source_id) params.set("source", target.source_id);
      const query = params.toString();
      return query ? `/memory?${query}` : "/memory";
    }
    case "concept": {
      const params = new URLSearchParams();
      params.set("concept", target.concept_id);
      if (target.workspace_id) params.set("ws", target.workspace_id);
      return `/knowledge?${params.toString()}`;
    }
    case "task":
      return `/orchestration?task=${encodeURIComponent(target.task_id)}`;
    case "teaching_proposal":
      return `/insights?proposal=${encodeURIComponent(target.proposal_id)}`;
    // --- §20.3 完整扩展（B01）---
    case "dashboard_view": {
      const params = new URLSearchParams();
      if (target.workspace_id) params.set("ws", target.workspace_id);
      params.set("range", target.range || "7d");
      return `/dashboard?${params.toString()}`;
    }
    case "chat_message":
      return `/chat/${encodeURIComponent(target.session_id)}`
        + `?message=${encodeURIComponent(target.message_id)}`;
    case "file": {
      const params = new URLSearchParams();
      if (target.folder_id) params.set("folder", target.folder_id);
      params.set("file", target.file_id);
      if (target.page != null) params.set("page", String(target.page));
      return `/resources/files?${params.toString()}`;
    }
    case "file_folder":
      return `/resources/files?folder=${encodeURIComponent(target.folder_id)}`;
    case "textbook": {
      const params = new URLSearchParams();
      params.set("textbook", target.textbook_id);
      if (target.volume_id) params.set("volume", target.volume_id);
      if (target.chapter_id) params.set("chapter", target.chapter_id);
      return `/resources/textbooks?${params.toString()}`;
    }
    case "assessment_view": {
      const params = new URLSearchParams();
      params.set("view", target.view || "start");
      if (target.assessment_id) params.set("assessment", target.assessment_id);
      if (target.source_id) params.set("source", target.source_id);
      if (target.question_id) params.set("question", target.question_id);
      return `/assessment?${params.toString()}`;
    }
    case "goal":
      return `/orchestration?goal=${encodeURIComponent(target.goal_id)}`;
    case "week_task": {
      const params = new URLSearchParams();
      params.set("week", String(Math.max(0, target.week_index || 0)));
      params.set("task", target.week_task_id);
      return `/orchestration?${params.toString()}`;
    }
    case "memory_preferences":
      return "/memory?section=preferences";
    case "note_revision":
      return `/notes/${encodeURIComponent(target.note_id)}`
        + `?revision=${Math.max(1, target.revision || 1)}`;
    case "archive_item":
      return `/archive?type=${encodeURIComponent(target.resource_type)}`
        + `&item=${encodeURIComponent(target.item_id)}`;
    case "profile_section":
      if (!target.section || target.section === "account") return "/account";
      if (target.section === "voice" || target.section === "assistant") return `/settings?section=${target.section}`;
      return "/profile?section=learning";
    case "settings_section":
      return `/settings?section=${encodeURIComponent(target.section || "general")}`;
    case "teaching_guidance":
      return `/insights?guidance=${encodeURIComponent(target.guidance_id)}`;
    case "docs_section":
      return `/docs?section=${encodeURIComponent(target.section_id)}`;
    case "admin_section":
      return `/admin?section=${encodeURIComponent(target.section)}`;
    default:
      return null;
  }
}

/** 笔记深链：/notes/{noteId}（无 id 落列表页）。 */
export function noteUrl(noteId?: string | null): string {
  return noteId ? `/notes/${encodeURIComponent(noteId)}` : "/notes";
}

/** URL 到达检查：精确路径与目标查询参数；根路径不匹配任何子页面。
 * 这只是必要条件，实体定位仍须由当前页面适配器确认。
 */
export function urlMatchesTarget(actual: string, target: string): boolean {
  try {
    const origin = "http://assistant.local";
    const current = new URL(actual, origin);
    const wanted = new URL(target, origin);
    if (current.origin !== wanted.origin || current.pathname !== wanted.pathname) return false;
    for (const key of new Set(wanted.searchParams.keys())) {
      const values = wanted.searchParams.getAll(key);
      const found = current.searchParams.getAll(key);
      if (values.length !== found.length || values.some((v, i) => v !== found[i])) return false;
    }
    return !wanted.hash || current.hash === wanted.hash;
  } catch {
    return false;
  }
}
