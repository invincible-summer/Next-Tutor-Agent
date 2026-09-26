"use client";
/* 工作区课堂列表（plan.md §3.3 / E01-E02）。
 * E01：路由 + 模式条 + 基础课程卡；E02 补三态筛选、Pager、CreateLessonModal
 * （?create=1 深链直达）与能力不足提示。ID 一律 encodeURIComponent。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft, ChevronRight, FileWarning, Loader2, Presentation, RefreshCw, Sparkles,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import { getWorkspace } from "@/lib/api";
import {
  ClassroomApiError, listLessons,
} from "@/lib/api-classroom";
import type { LessonSummaryPublic } from "@/lib/types-classroom.generated";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { WorkspaceModeBar } from "@/components/classroom/WorkspaceModeBar";
import { STRINGS } from "./strings";
import { Suspense } from "react";

function lessonHref(workspaceId: string, lessonId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}` +
    `/classroom/${encodeURIComponent(lessonId)}`;
}

function statusLabel(tr: (k: string, fb?: string) => string,
  status: string): string {
  return tr(`cls.status.${status}`, status);
}

function statusCls(status: string): string {
  switch (status) {
    case "generating":
      return "border-accent/30 bg-accent-soft/40 text-accent-strong";
    case "ready":
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400";
    case "needs_attention":
      return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400";
    case "failed":
      return "border-danger/30 bg-danger/10 text-danger";
    default:
      return "border-border bg-surface-hover text-muted";
  }
}

function LessonCard({ wsId, lesson, tr }: {
  wsId: string; lesson: LessonSummaryPublic;
  tr: (k: string, fb?: string) => string;
}) {
  const updated = useMemo(() => {
    const d = new Date(lesson.updated_at);
    return Number.isNaN(d.getTime()) ? "" :
      d.toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }, [lesson.updated_at]);
  const generating = lesson.status === "generating";
  return (
    <Link
      href={lessonHref(wsId, lesson.lesson_id)}
      className="group block rounded-[12px] border border-border bg-surface p-4 shadow-sm transition-all hover:border-accent/40 hover:shadow"
    >
      <div className="flex items-start gap-3">
        <div className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px]",
          generating ? "bg-accent-soft text-accent-strong" : "bg-accent-soft/60 text-accent",
        )}>
          {generating
            ? <Loader2 size={16} className="animate-spin" />
            : <Presentation size={16} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="min-w-0 flex-1 truncate text-[0.9rem] font-semibold text-fg">
              {lesson.title}
            </h3>
            <span className={cn(
              "shrink-0 rounded-full border px-2 py-0.5 text-[0.65rem] font-medium",
              statusCls(lesson.status),
            )}>
              {statusLabel(tr, lesson.status)}
            </span>
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.7rem] text-muted">
            {lesson.latest_ready_revision != null && (
              <span className="tnum">
                {tr("cls.card.revision").replace("%n", String(lesson.latest_ready_revision))}
              </span>
            )}
            {lesson.brief && (
              <>
                <span>{tr("cls.card.minutes").replace("%n", String(lesson.brief.duration_minutes))}</span>
                {lesson.brief.grade && <span>{lesson.brief.grade}</span>}
              </>
            )}
            {updated && <span className="tnum">{updated}</span>}
          </p>
        </div>
        <ChevronRight size={16} className="mt-1.5 shrink-0 text-muted/40 transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
      </div>
    </Link>
  );
}

function ClassroomListInner() {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const router = useRouter();
  const params = useParams<{ workspaceId: string }>();
  // 动态段可能是仍编码的中文 slug：手动解码一次（与 chat 路由同模式），
  // 构造链接时再 encodeURIComponent，绝不二次解码。
  const safeDecode = (s: string) => {
    try { return decodeURIComponent(s); } catch { return s; }
  };
  const workspaceId = safeDecode(params.workspaceId ?? "");

  const [wsName, setWsName] = useState("");
  const [lessons, setLessons] = useState<LessonSummaryPublic[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  // 工作区不存在：给明确出口，而不是空列表假象。
  const [wsMissing, setWsMissing] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    setFailed(false);
    listLessons(workspaceId, { pageSize: 20 })
      .then((res) => setLessons(res.items ?? []))
      .catch((err) => {
        // 课堂关闭/未开放：列表保持空，不弹错误风暴。
        if (err instanceof ClassroomApiError && err.code === "classroom_disabled") {
          setLessons([]);
        } else {
          setFailed(true);
        }
      })
      .finally(() => setLoading(false));
  }, [workspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    // 微任务延迟（setState 不在 effect 体内同步触发；与 Sidebar 同模式）。
    const id = setTimeout(() => refresh(), 0);
    getWorkspace(workspaceId)
      .then((ws) => { setWsName(ws.name); setWsMissing(false); })
      .catch(() => setWsMissing(true));
    return () => clearTimeout(id);
  }, [workspaceId, refresh]);

  if (wsMissing) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <EmptyState
          icon={<FileWarning size={28} />}
          title={tr("ws.notfound.title")}
          desc={tr("ws.notfound.desc")}
          action={<Link href="/chat"><Button variant="outline" icon={<ArrowLeft size={14} />}>{tr("cls.back.to.list")}</Button></Link>}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* 顶部：返回 + 标题 + 模式条 + 主操作 */}
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
        <Link
          href="/chat"
          className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          aria-label="back to chat"
        >
          <ArrowLeft size={16} />
        </Link>
        <div className="min-w-0">
          <h1 className="truncate font-serif text-[1.05rem] font-bold tracking-tight text-fg">
            {wsName ? `${wsName} · ` : ""}{tr("cls.list.title.suffix")}
          </h1>
          <p className="hidden text-[0.68rem] text-muted sm:block">{tr("cls.list.subtitle")}</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <WorkspaceModeBar workspaceId={workspaceId} mode="classroom" />
          <Button
            icon={<Sparkles size={14} />}
            onClick={() => router.push("?create=1")}
          >
            {tr("cls.create")}
          </Button>
        </div>
      </header>

      {/* 课程卡列表（E02 加筛选/Pager） */}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted">
            <Loader2 size={16} className="animate-spin" />
          </div>
        ) : failed ? (
          <EmptyState
            icon={<FileWarning size={28} />}
            title={tr("cls.error.load")}
            action={<Button variant="outline" size="sm" icon={<RefreshCw size={13} />} onClick={refresh}>{tr("cls.retry")}</Button>}
          />
        ) : lessons.length === 0 ? (
          <EmptyState
            icon={<Presentation size={28} />}
            title={tr("cls.empty.title")}
            desc={tr("cls.empty.desc")}
          />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-2.5">
            {lessons.map((lesson) => (
              <LessonCard key={lesson.lesson_id} wsId={workspaceId} lesson={lesson} tr={tr} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ClassroomListPage() {
  return (
    <Suspense fallback={<div className="h-full w-full bg-bg" />}>
      <ClassroomListInner />
    </Suspense>
  );
}
