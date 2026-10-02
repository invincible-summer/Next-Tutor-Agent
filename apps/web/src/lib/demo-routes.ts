import { readFileSync } from "node:fs";
import { join } from "node:path";

export function demoRoutes(): {
  sessions: string[]; notes: string[]; workspaces: string[];
  lessons: { workspaceId: string; lessonId: string }[];
  runs: { workspaceId: string; lessonId: string; runId: string }[];
} {
  if (process.env.NEXT_PUBLIC_DEMO_MODE !== "1") return { sessions: [], notes: [], workspaces: [], lessons: [], runs: [] };
  return JSON.parse(readFileSync(join(process.cwd(), "public/demo/manifest.json"), "utf8")).routes;
}
