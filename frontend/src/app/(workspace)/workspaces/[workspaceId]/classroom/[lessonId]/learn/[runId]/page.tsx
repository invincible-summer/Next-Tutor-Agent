"use client";
/* 课堂播放器路由（plan.md §3.1）：已创建 run 的播放页，刷新不重建 run。
 * E01 只落地路由与占位；reducer/audio-controller/lease 在 G 阶段实现。
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, PlayCircle } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { WorkspaceModeBar } from "@/components/classroom/WorkspaceModeBar";
import { STRINGS } from "../../../strings";

export default function LearnRunPage() {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const params = useParams<{ workspaceId: string; lessonId: string; runId: string }>();
  const safeDecode = (s: string) => {
    try { return decodeURIComponent(s); } catch { return s; }
  };
  const workspaceId = safeDecode(params.workspaceId ?? "");
  const lessonId = safeDecode(params.lessonId ?? "");
  const runId = safeDecode(params.runId ?? "");
  const lessonHref = `/workspaces/${encodeURIComponent(workspaceId)}` +
    `/classroom/${encodeURIComponent(lessonId)}`;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-border px-5 py-3">
        <Link
          href={lessonHref}
          className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          aria-label={tr("cls.back.to.list")}
        >
          <ArrowLeft size={16} />
        </Link>
        <h1 className="min-w-0 truncate font-serif text-[1.05rem] font-bold tracking-tight text-fg">
          {tr("cls.learn.player.title")}
        </h1>
        <div className="ml-auto flex items-center gap-2">
          <WorkspaceModeBar workspaceId={workspaceId} mode="classroom" />
        </div>
      </header>
      <div className="flex min-h-0 flex-1 items-center justify-center p-6">
        <EmptyState
          icon={<PlayCircle size={28} />}
          title={tr("cls.learn.coming.title")}
          desc={tr("cls.learn.coming.desc")}
          action={
            <Link href={lessonHref}>
              <Button variant="outline" icon={<ArrowLeft size={14} />}>
                {tr("cls.detail.preview")}
              </Button>
            </Link>
          }
        />
        <span className="sr-only">{`${tr("cls.learn.run")}: ${runId}`}</span>
      </div>
    </div>
  );
}
