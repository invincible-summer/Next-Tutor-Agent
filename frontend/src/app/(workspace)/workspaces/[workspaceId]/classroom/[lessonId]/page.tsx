"use client";
/* 课程详情/预览路由（plan.md §3.1 / §4.3；E01 骨架，E04 完整编辑器）。
 * ?revision=N 固定预览版本；缺省最新已发布版本。预览绝不创建 run、
 * 不触发 TTS。E01 展示已发布 revision 的首帧预览与生成中的任务状态；
 * 左缩略图/右侧讲稿栏与单页操作在 E04 落地。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import {
  ArrowLeft, FileQuestion, Loader2, Presentation,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { ClassroomApiError, getLesson, getRevisionFrame } from "@/lib/api-classroom";
import type { LessonDetailPublic } from "@/lib/types-classroom.generated";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { WorkspaceModeBar } from "@/components/classroom/WorkspaceModeBar";
import SlideFrame from "@/components/classroom/SlideFrame";
import { STRINGS } from "../strings";

const PHASE_LABELS_ZH: Record<string, string> = {
  resolve_sources: "读取教材",
  research: "查找补充资料",
  outline: "组织课程",
  visual_assets: "配图",
  author_slides: "编写课件与讲稿",
  checkpoints: "随堂检查",
  review: "整体校验",
  render: "检查排版",
  publish: "准备开课",
};

function LessonDetailInner() {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const params = useParams<{ workspaceId: string; lessonId: string }>();
  const searchParams = useSearchParams();
  const safeDecode = (s: string) => {
    try { return decodeURIComponent(s); } catch { return s; }
  };
  const workspaceId = safeDecode(params.workspaceId ?? "");
  const lessonId = safeDecode(params.lessonId ?? "");
  const revParam = searchParams.get("revision");
  const fixedRevision = revParam && /^\d+$/.test(revParam) ? Number(revParam) : undefined;

  const [detail, setDetail] = useState<LessonDetailPublic | null>(null);
  const [frameHtml, setFrameHtml] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setMissing(false);
    getLesson(workspaceId, lessonId, fixedRevision)
      .then((d) => {
        setDetail(d);
        const rev = d.revision?.revision;
        if (rev != null) {
          return getRevisionFrame(workspaceId, lessonId, rev)
            .then(setFrameHtml)
            .catch(() => setFrameHtml(null));
        }
        setFrameHtml(null);
        return undefined;
      })
      .catch((err) => {
        if (err instanceof ClassroomApiError &&
            (err.code === "source_not_found" || err.code === "classroom_disabled")) {
          setMissing(true);
        } else {
          setMissing(true);
        }
      })
      .finally(() => setLoading(false));
  }, [workspaceId, lessonId, fixedRevision]);

  // 微任务延迟首载（setState 不在 effect 体内同步触发；后台标签页 rAF 不可用）。
  useEffect(() => {
    const id = setTimeout(() => load(), 0);
    return () => clearTimeout(id);
  }, [load]);

  const phaseLabel = useMemo(() => {
    const phase = detail?.latest_job?.phase ?? detail?.pending?.phase ?? null;
    if (!phase) return "";
    return PHASE_LABELS_ZH[phase] ?? phase;
  }, [detail]);

  const backHref = `/workspaces/${encodeURIComponent(workspaceId)}/classroom`;

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        <Loader2 size={18} className="animate-spin" />
      </div>
    );
  }

  if (missing || !detail) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <EmptyState
          icon={<FileQuestion size={28} />}
          title={tr("cls.detail.notfound")}
          action={<Link href={backHref}><Button variant="outline" icon={<ArrowLeft size={14} />}>{tr("cls.back.to.list")}</Button></Link>}
        />
      </div>
    );
  }

  const generating = detail.revision == null;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
        <Link
          href={backHref}
          className="flex h-8 w-8 items-center justify-center rounded-[8px] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
          aria-label={tr("cls.back.to.list")}
        >
          <ArrowLeft size={16} />
        </Link>
        <h1 className="min-w-0 truncate font-serif text-[1.05rem] font-bold tracking-tight text-fg">
          {detail.title}
        </h1>
        <div className="ml-auto flex items-center gap-2">
          <WorkspaceModeBar workspaceId={workspaceId} mode="classroom" />
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        {generating ? (
          <div className="mx-auto max-w-2xl">
            <EmptyState
              icon={detail.latest_job?.state === "failed"
                ? <FileQuestion size={28} /> : <Loader2 size={28} className="animate-spin" />}
              title={tr("cls.detail.noready.title")}
              desc={phaseLabel
                ? `${tr("cls.detail.noready.desc")}`
                : tr("cls.detail.noready.desc")}
            />
            {phaseLabel && (
              <p className="mt-2 text-center text-[0.75rem] text-muted">
                {tr("cls.detail.phase")}：{phaseLabel}
                {detail.pending?.progress?.total_slides
                  ? ` · ${detail.pending.progress.completed_slides ?? 0}/${detail.pending.progress.total_slides}`
                  : ""}
              </p>
            )}
          </div>
        ) : frameHtml ? (
          <div className="mx-auto max-w-5xl">
            <p className="mb-2 text-[0.7rem] font-medium uppercase tracking-[0.14em] text-muted/70">
              {tr("cls.detail.preview")}
            </p>
            <SlideFrame
              html={frameHtml}
              title={detail.title}
              className="aspect-video w-full overflow-hidden rounded-[12px] border border-border bg-white shadow-md"
            />
          </div>
        ) : (
          <EmptyState
            icon={<Presentation size={28} />}
            title={tr("cls.detail.preview")}
            desc={tr("cls.error.load")}
            action={<Button variant="outline" size="sm" onClick={load}>{tr("cls.retry")}</Button>}
          />
        )}
      </div>
    </div>
  );
}

export default function LessonDetailPage() {
  return (
    <Suspense fallback={<div className="h-full w-full bg-bg" />}>
      <LessonDetailInner />
    </Suspense>
  );
}
