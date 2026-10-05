import type { Href } from "expo-router";
import { domainRoute, type DomainTarget } from "./routes";
function id(value: unknown): value is string {
  return typeof value === "string" && /^[\w.:-]{1,128}$/.test(value);
}
export function assistantTarget(
  value: unknown,
): { href: Href; workspaceId?: string } | null {
  if (!value || typeof value !== "object") return null;
  const t = value as Record<string, unknown>;
  const kind = t.kind;
  const workspaceId = id(t.workspace_id) ? t.workspace_id : undefined;
  const result = (href: Href) =>
    workspaceId ? { href, workspaceId } : { href };
  if (kind === "chat_session" && id(t.session_id))
    return result(domainRoute({ kind: "chat", id: t.session_id }));
  if (kind === "workspace_chat" && workspaceId)
    return result(domainRoute({ kind: "chat", workspaceId }));
  if (
    (kind === "lesson" || kind === "classroom_run") &&
    id(t.lesson_id) &&
    workspaceId
  )
    return result({
      pathname: "/(main)/learn/courses/[lessonId]",
      params: {
        lessonId: t.lesson_id,
        ws: workspaceId,
        ...(id(t.run_id) ? { runId: t.run_id } : {}),
      },
    });
  if (kind === "workspace_courses" && workspaceId)
    return result(domainRoute({ kind: "lesson" }));
  if ((kind === "note" || kind === "note_revision") && id(t.note_id))
    return result(domainRoute({ kind: "note", id: t.note_id }));
  if (kind === "concept" && id(t.concept_id))
    return result(domainRoute({ kind: "concept", id: t.concept_id }));
  if (kind === "learning_archive")
    return result(domainRoute({ kind: "insights" }));
  if (kind === "task" || kind === "goal" || kind === "week_task")
    return result(domainRoute({ kind: "plan" }));
  if (kind === "file" || kind === "file_folder" || kind === "textbook")
    return result(domainRoute({ kind: "resources" }));
  if (kind === "archive_item") return result(domainRoute({ kind: "archive" }));
  if (kind === "assessment_view")
    return result(domainRoute({ kind: "assessment" }));
  if (kind === "memory_preferences")
    return result(domainRoute({ kind: "memory" }));
  if (kind === "module" || typeof t.route_id === "string") {
    const modules: Record<string, DomainTarget["kind"]> = {
      chat: "chat",
      course: "lesson",
      notes: "note",
      knowledge: "concept",
      orchestration: "plan",
      assessment: "assessment",
      tools: "illustration",
      tools_illustration: "illustration",
      memory: "memory",
      resources_files: "resources",
      resources_textbooks: "resources",
      archive: "archive",
      profile: "profile",
      insights: "insights",
    };
    if (t.route_id === "home" || t.route_id === "dashboard")
      return { href: "/" };
    if (t.route_id === "account" || t.route_id === "settings")
      return { href: `/(main)/me/${t.route_id}` as Href };
    const key = modules[String(t.route_id)];
    if (key) return result(domainRoute({ kind: key } as DomainTarget));
  }
  return null;
}
