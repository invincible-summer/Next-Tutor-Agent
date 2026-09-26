"use client";
// 对话 | 课堂 模式条（plan.md §3.2.2-3）：工作区内的两个真实入口。
// 必须是真实链接 + aria-current（键盘可达、可中键新开），不是同页 Tab。
// 「对话」回到该区最近访问且仍存在的普通会话，否则 /chat?ws=<id>。
import { useMemo } from "react";
import Link from "next/link";
import { MessageSquareText, Presentation } from "lucide-react";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
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

export function chatPathForWorkspace(workspaceId: string,
  sessions: SessionItem[]): string {
  const latest = latestSessionInWorkspace(sessions, workspaceId);
  if (latest) return `/chat/${encodeURIComponent(latest.session_id)}`;
  return `/chat?ws=${encodeURIComponent(workspaceId)}`;
}

export function WorkspaceModeBar({ workspaceId, workspaceName, mode,
  sessions, className }: {
  workspaceId: string;
  /** 展示用；窄屏隐藏（CSS 控制），不影响链接。 */
  workspaceName?: string;
  mode: "chat" | "classroom";
  /** 「对话」链接定位最近会话用；缺省时落到 /chat?ws=。 */
  sessions?: SessionItem[];
  className?: string;
}) {
  const chatHref = useMemo(
    () => chatPathForWorkspace(workspaceId, sessions ?? []),
    [workspaceId, sessions]);
  const classroomHref = classroomPath(workspaceId);
  const { lang } = useUIStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  return (
    <nav
      aria-label="workspace modes"
      className={cn(
        "flex min-w-0 items-center gap-1.5 rounded-full border border-border bg-surface/80 px-1 py-0.5 shadow-sm backdrop-blur",
        className,
      )}
    >
      {workspaceName && (
        <span className="hidden max-w-40 truncate pl-2 pr-1 text-[0.72rem] font-medium text-fg-secondary md:inline">
          {workspaceName}
          <span className="mx-1 text-muted/60">·</span>
        </span>
      )}
      <Link
        href={chatHref}
        aria-current={mode === "chat" ? "page" : undefined}
        prefetch={false}
        className={cn(
          "flex items-center gap-1 rounded-full px-2.5 py-1 text-[0.72rem] font-medium transition-colors",
          mode === "chat"
            ? "bg-accent-soft text-accent-strong"
            : "text-muted hover:bg-surface-hover hover:text-fg-secondary",
        )}
      >
        <MessageSquareText size={12} />
        <span data-mode-chat-label>{tr("ws.mode.chat", "对话")}</span>
      </Link>
      <Link
        href={classroomHref}
        aria-current={mode === "classroom" ? "page" : undefined}
        prefetch={false}
        className={cn(
          "flex items-center gap-1 rounded-full px-2.5 py-1 text-[0.72rem] font-medium transition-colors",
          mode === "classroom"
            ? "bg-accent-soft text-accent-strong"
            : "text-muted hover:bg-surface-hover hover:text-fg-secondary",
        )}
      >
        <Presentation size={12} />
        <span data-mode-classroom-label>{tr("ws.mode.classroom", "课堂")}</span>
      </Link>
    </nav>
  );
}
