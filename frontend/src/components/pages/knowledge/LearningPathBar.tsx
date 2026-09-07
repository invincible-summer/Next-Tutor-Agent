"use client";
// 底部推荐学习路径条：getLearningPath 的 next_to_learn 前 6 个，点击预填提问跳 /chat。
import { useRouter } from "next/navigation";
import { Route } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/EmptyState";
import type { LearningPathResp, PathNode } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

function nodeName(n: PathNode): string {
  return n.name ?? n.concept ?? n.skill_id ?? "";
}

/** 难度点：1–5 个小圆点。 */
function DiffDots({ d }: { d: number }) {
  const n = Math.min(5, Math.max(1, Math.round(d)));
  return (
    <span className="flex items-center gap-[2.5px]" aria-hidden>
      {Array.from({ length: 5 }, (_, i) => (
        <span
          key={i}
          className={`h-[3.5px] w-[3.5px] rounded-full ${i < n ? "bg-accent" : "bg-border"}`}
        />
      ))}
    </span>
  );
}

export function LearningPathBar({
  tr,
  path,
  loading,
  error,
  onRetry,
}: {
  tr: Tr;
  path: LearningPathResp | null;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const router = useRouter();
  const items = (path?.next_to_learn ?? []).filter((n) => nodeName(n)).slice(0, 6);

  return (
    <Card pad={false} className="shrink-0 px-4 py-2.5">
      <div className="flex items-center gap-3 overflow-x-auto">
        <div className="flex shrink-0 items-center gap-1.5">
          <Route size={14} className="text-accent" />
          <span className="text-xs font-semibold text-fg">{tr("pathTitle")}</span>
        </div>
        {loading && <Skeleton className="h-6 w-72" />}
        {!loading && error && (
          <span className="text-xs text-muted">
            {tr("pathFail")}
            <button onClick={onRetry} className="ml-2 cursor-pointer text-xs font-medium text-accent underline">
              {tr("chat.retry", "重试")}
            </button>
          </span>
        )}
        {!loading && !error && path?.status === "disabled" && (
          <span className="text-xs text-muted">{tr("pathDisabled")}</span>
        )}
        {!loading && !error && path?.status !== "disabled" && items.length === 0 && (
          <span className="text-xs text-muted">{tr("pathEmpty")}</span>
        )}
        {!loading &&
          !error &&
          items.map((n, i) => {
            const name = nodeName(n);
            const reason = typeof n.reason === "string" ? n.reason : "";
            // auto-send 深链：问句带推荐理由，跳过去直接开聊。
            const msg = reason
              ? tr("askMsgReason").replace("%c", name).replace("%r", reason)
              : tr("askMsg").replace("%c", name);
            return (
              <button
                key={`${name}-${i}`}
                title={reason ? `${name} · ${reason}` : name}
                onClick={() => router.push(`/chat?q=${encodeURIComponent(msg)}&send=1`)}
                className="flex shrink-0 cursor-pointer items-center gap-2 rounded-full border border-border bg-surface px-2.5 py-1 text-xs text-fg-secondary transition-colors hover:border-accent hover:text-accent"
              >
                {reason && (
                  <span className="max-w-16 truncate rounded bg-accent-soft px-1 py-px text-[9px] leading-tight text-accent-strong">
                    {reason}
                  </span>
                )}
                <span>{name}</span>
                <DiffDots d={typeof n.difficulty === "number" ? n.difficulty : 3} />
              </button>
            );
          })}
      </div>
    </Card>
  );
}
