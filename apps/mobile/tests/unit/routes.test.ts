import { assistantTarget } from "@/shell/assistant-target";
import {
  consumeDestination,
  domainRoute,
  rememberDestination,
  safeProductPath,
} from "@/shell/routes";

describe("mobile product navigation", () => {
  afterEach(() => consumeDestination());

  test.each([
    "/",
    "/tutor",
    "/tutor/session-1",
    "/learn/assessment",
    "/learn/plan",
    "/learn/courses/lesson-1",
    "/library/resources",
    "/library/workspaces",
    "/library/notes/note-1",
    "/library/knowledge",
    "/library/diagrams",
    "/library/tools/illustration",
    "/me/account",
    "/me/settings",
  ])("allows known relative product destination %s", (path) => {
    expect(safeProductPath(path)).toBe(path);
  });

  test.each([
    "https://example.invalid/tutor",
    "//example.invalid/tutor",
    "/admin",
    "/docs",
    "/api/v1/auth",
    "/library/notes/../../me",
    "/tutor/a\\b",
    "/tutor/a\n",
    "/(auth)/sign-in",
    "javascript:alert(1)",
    "/learn/unknown",
  ])("rejects external or unknown destination %s", (path) => {
    expect(safeProductPath(path)).toBeNull();
  });

  test("authentication preserves only known query parameters once", () => {
    rememberDestination(
      "/learn/courses/lesson-1?ws=workspace-1&runId=run-1&token=secret&url=https://example.invalid",
    );
    expect(consumeDestination()).toBe(
      "/learn/courses/lesson-1?ws=workspace-1&runId=run-1",
    );
    expect(consumeDestination()).toBe("/");
  });

  test("prefill is bounded, kept only for chat, and never interpreted as a route", () => {
    const result = String(safeProductPath("/tutor?q=" + "a".repeat(5000)));
    expect(new URLSearchParams(result.split("?")[1]).get("q")).toHaveLength(
      4000,
    );
    expect(safeProductPath("/me/profile?q=private&ws=bad/path")).toBe(
      "/me/profile",
    );
  });

  test("domain route maps identifiers into structured parameters", () => {
    expect(
      domainRoute({
        kind: "lesson",
        id: "lesson-1",
        runId: "run-1",
        workspaceId: "workspace-1",
      }),
    ).toEqual({
      pathname: "/(main)/learn/courses/[lessonId]",
      params: { lessonId: "lesson-1", runId: "run-1", ws: "workspace-1" },
    });
  });
});

describe("typed assistant targets", () => {
  test("classroom target requires explicit workspace and maps its run", () => {
    expect(
      assistantTarget({
        kind: "classroom_run",
        workspace_id: "workspace-1",
        lesson_id: "lesson-1",
        run_id: "run-1",
        url: "https://ignored.invalid",
      }),
    ).toEqual({
      href: {
        pathname: "/(main)/learn/courses/[lessonId]",
        params: { lessonId: "lesson-1", ws: "workspace-1", runId: "run-1" },
      },
      workspaceId: "workspace-1",
    });
    expect(
      assistantTarget({ kind: "classroom_run", lesson_id: "lesson-1" }),
    ).toBeNull();
  });

  test.each([
    null,
    { kind: "url", url: "https://example.invalid" },
    { kind: "module", route_id: "admin" },
    { kind: "module", route_id: "docs" },
    { kind: "chat_session", session_id: "../secret" },
    { kind: "note", note_id: "note/secret" },
    { kind: "native_command", command: "open_url" },
  ])("rejects unsupported target %#", (value) =>
    expect(assistantTarget(value)).toBeNull(),
  );

  test("supported module routes remain product owned", () => {
    expect(assistantTarget({ kind: "module", route_id: "notes" })).toEqual({
      href: "/(main)/library/notes",
    });
    expect(assistantTarget({ kind: "note", note_id: "note-1" })).toEqual({
      href: {
        pathname: "/(main)/library/notes/[noteId]",
        params: { noteId: "note-1" },
      },
    });
  });
});
