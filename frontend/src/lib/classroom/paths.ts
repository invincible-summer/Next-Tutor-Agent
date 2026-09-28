/* 课堂/对话路由助手。ID 一律 encodeURIComponent；会话定位取最近更新的同区会话。 */
import type { SessionItem } from "@/lib/types";

export function latestSessionInWorkspace(
  sessions: SessionItem[], workspaceId: string,
): SessionItem | null {
  let best: SessionItem | null = null;
  for (const s of sessions) {
    if (s.workspace_id !== workspaceId) continue;
    if (!best || s.updated_at > best.updated_at) best = s;
  }
  return best;
}

export function classroomPath(workspaceId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}/classroom`;
}

export function lessonPath(workspaceId: string, lessonId: string): string {
  return `${classroomPath(workspaceId)}/${encodeURIComponent(lessonId)}`;
}

export function chatPathForWorkspace(workspaceId: string,
  sessions: SessionItem[]): string {
  const latest = latestSessionInWorkspace(sessions, workspaceId);
  if (latest) return `/chat/${encodeURIComponent(latest.session_id)}`;
  return `/chat?ws=${encodeURIComponent(workspaceId)}`;
}
