import type { Href } from "expo-router";
export type DomainTarget =
  | { kind: "chat"; id?: string; workspaceId?: string; text?: string }
  | { kind: "note"; id?: string }
  | { kind: "lesson"; id?: string; runId?: string; workspaceId?: string }
  | { kind: "concept"; id?: string }
  | {
      kind:
        | "assessment"
        | "plan"
        | "resources"
        | "diagrams"
        | "illustration"
        | "worksheet"
        | "profile"
        | "insights"
        | "memory"
        | "archive";
    };
export function domainRoute(target: DomainTarget): Href {
  if (target.kind === "chat")
    return {
      pathname: target.id ? "/(main)/tutor/[sessionId]" : "/(main)/tutor",
      params: {
        ...(target.id ? { sessionId: target.id } : {}),
        ...(target.workspaceId ? { ws: target.workspaceId } : {}),
        ...(target.text ? { q: target.text } : {}),
      },
    };
  if (target.kind === "note")
    return target.id
      ? {
          pathname: "/(main)/library/notes/[noteId]",
          params: { noteId: target.id },
        }
      : "/(main)/library/notes";
  if (target.kind === "lesson")
    return target.id
      ? {
          pathname: "/(main)/learn/courses/[lessonId]",
          params: {
            lessonId: target.id,
            ...(target.runId ? { runId: target.runId } : {}),
            ...(target.workspaceId ? { ws: target.workspaceId } : {}),
          },
        }
      : "/(main)/learn/courses";
  if (target.kind === "concept")
    return {
      pathname: "/(main)/library/knowledge",
      params: target.id ? { conceptId: target.id } : {},
    };
  const routes = {
    assessment: "/(main)/learn/assessment",
    plan: "/(main)/learn/plan",
    resources: "/(main)/library/resources",
    diagrams: "/(main)/library/diagrams",
    illustration: "/(main)/library/tools/illustration",
    worksheet: "/(main)/library/tools/worksheet",
    profile: "/(main)/me/profile",
    insights: "/(main)/me/insights",
    memory: "/(main)/me/memory",
    archive: "/(main)/me/archive",
  } as const;
  return routes[target.kind];
}
const ID = /^[\w-]{1,160}$/;
/** Only relative, known destinations and explicitly allowed parameters survive authentication. */
export function safeProductPath(path: string): Href | null {
  const [pathname, query = ""] = path.split("?");
  if (
    pathname?.includes("://") ||
    path.startsWith("//") ||
    /[\\\u0000-\u001f]/.test(path)
  )
    return null;
  if (
    !pathname ||
    !/^\/(?:tutor(?:\/[\w-]+)?|learn(?:\/(?:assessment|plan|courses)(?:\/[\w-]+)?)?|library(?:\/(?:resources|workspaces|notes(?:\/[\w-]+)?|knowledge|diagrams|tools(?:\/(?:illustration|worksheet))?))?|me(?:\/(?:profile|insights|memory|archive|account|settings))?)?$/.test(
      pathname,
    )
  )
    return null;
  const input = new URLSearchParams(query);
  const allowed = new URLSearchParams();
  for (const key of ["ws", "runId", "conceptId"]) {
    const value = input.get(key);
    if (value && ID.test(value)) allowed.set(key, value);
  }
  if (pathname.startsWith("/tutor")) {
    const text = input.get("q");
    if (text) allowed.set("q", text.slice(0, 4000));
  }
  return (pathname + (allowed.size ? "?" + allowed.toString() : "")) as Href;
}
let pending: Href | null = null;
export function rememberDestination(path: string) {
  pending = safeProductPath(path);
}
export function consumeDestination(): Href {
  const value = pending ?? "/";
  pending = null;
  return value;
}
