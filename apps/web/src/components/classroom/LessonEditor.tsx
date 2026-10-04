"use client";
/* 课程预览/编辑器（E04）。
 * 三栏：左缩略目录 | 中课件舞台 | 右「讲稿 | 来源 | 设置」。默认只打开
 * 当前页讲稿。所有修改以 base_revision 做 CAS；revision_conflict 显示
 * 「内容已更新」并允许重载。零 LLM 操作（换主题/排序/删页/换图/改文字）
 * 走快速修订；单页重生成（更详细/更简洁/换个例子）展示生成进度。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ArrowDown, ArrowUp, ChevronDown, ChevronLeft, ChevronRight, Download, FileText, Printer,
  Image as ImageIcon, List, Loader2, Pencil, RefreshCw, Sparkles, Trash2, X,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import {
  ClassroomApiError, createRevision, getJob, imageSearch, retryJob,
} from "@/lib/api-classroom";
import type {
  BulletsBlock, ChangeThemeOperation, ImageBlock, ImageCandidate, JobPublic, LessonDetailPublic,
  RevisionOperation, RevisionPublic, SlideSpec, ThemeTemplateInfo,
} from "@next-tutor/contracts/classroom";
import { Button } from "@/components/ui/Button";
import { Input, Textarea, Field } from "@/components/ui/Input";
import SlideFrame, { type SlideFrameHandle } from "./SlideFrame";
import { GenerationProgress } from "./GenerationProgress";
import { TemplatePicker } from "./TemplatePicker";
import { Markdown } from "@/components/chat/markdown";
import { SlideThumbnail } from "./SlideThumbnail";
import { BlockEditor } from "./BlockEditor";
import { STRINGS } from "./strings";

/** 纯文本 span 拼接；含 emphasis/math 的要点保持只读（§4.3 不破坏公式）。 */
function spansToText(spans: Array<{ kind?: string; text?: string }>): string | null {
  const parts: string[] = [];
  for (const sp of spans) {
    if (sp.kind && sp.kind !== "text") return null;
    parts.push(sp.text ?? "");
  }
  return parts.join("");
}

const REGEN_PRESETS = [
  { key: "cls.regen.detail", instrKey: "cls.regen.instr.detail" },
  { key: "cls.regen.brief", instrKey: "cls.regen.instr.brief" },
  { key: "cls.regen.example", instrKey: "cls.regen.instr.example" },
] as const;

function editKey() { return `edit_${crypto.randomUUID()}`; }

type Tab = "content" | "script" | "sources" | "settings";

export function LessonEditor({ workspaceId, lessonId, detail, frameHtml,
  onReload, onDirtyChange }: {
  workspaceId: string;
  lessonId: string;
  detail: LessonDetailPublic;
  /** 自包含课件 HTML（父页面经鉴权 fetch 取得）。 */
  frameHtml: string;
  /** 重载课程（修订完成后拉最新 revision）。 */
  onReload: (revision?: number) => void;
  /** 未保存修改信号（组件/讲稿表单；助手导航保护 §5.4）。 */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const revision = detail.revision;
  const frameRef = useRef<SlideFrameHandle>(null);
  const [page, setPage] = useState(0); // 0 基
  const [tab, setTab] = useState<Tab>("content");
  const [mobilePanel, setMobilePanel] = useState<"outline" | Tab | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [regenJob, setRegenJob] = useState<JobPublic | null>(null);
  const [candidates, setCandidates] = useState<ImageCandidate[] | null>(null);
  // 组件/讲稿表单的未保存信号（§5.4 导航保护）；setState 引用稳定可直传。
  const [blockDirty, setBlockDirty] = useState(false);
  const [scriptDirty, setScriptDirty] = useState(false);
  useEffect(() => {
    onDirtyChange?.(blockDirty || scriptDirty);
  }, [blockDirty, scriptDirty, onDirtyChange]);

  const slides = useMemo(() => revision?.slides ?? [], [revision]);
  const current = slides[page];
  const activeJob = regenJob ?? (detail.latest_job && ["queued", "running", "awaiting_outline", "needs_input"].includes(detail.latest_job.state) ? detail.latest_job : null);
  const locked = Boolean(busy || activeJob);
  const tabLabel = (entry: Tab) => entry === "content" ? (lang === "en" ? "Content" : "组件") : tr(`cls.tab.${entry}`);
  const baseRevision = revision?.revision ?? 1;
  useEffect(() => {
    frameRef.current?.gotoPage(page + 1);
    frameRef.current?.setBlockState(["*"], []);
  }, [page, frameHtml]);

  // 页码越界（删除页后）回到末页：渲染期派生重置（React 官方模式）。
  const [lastCount, setLastCount] = useState(slides.length);
  if (slides.length !== lastCount) {
    setLastCount(slides.length);
    if (page > slides.length - 1) setPage(Math.max(0, slides.length - 1));
  }

  /** 提交修订操作；零 LLM 快速路径完成后自动重载最新版本。 */
  const runOperation = useCallback(async (operation: RevisionOperation,
    label: string, reloadRevision?: number) => {
    if (busy || activeJob) return;
    setBusy(label);
    setConflict(false);
    setNotice(null);
    try {
      const res = await createRevision(
        workspaceId, lessonId,
        { base_revision: reloadRevision ?? baseRevision,
          operation },
        editKey());
      // 轮询到终态（快速修订通常 <2s；重生成走 GenerationProgress）
      for (let i = 0; i < 300; i++) {
        const job = await getJob(workspaceId, lessonId, res.job_id).catch(() => null);
        if (job && ["succeeded", "failed", "cancelled"].includes(job.state)) {
          if (job.state !== "succeeded") {
            setNotice(`${tr("cls.edit.failed")}${job.last_error ? `：${job.last_error.slice(0, 120)}` : ""}`);
            onReload();
            return;
          }
          onReload();
          return;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
      setNotice(tr("cls.edit.slow"));
      onReload();
    } catch (e) {
      if (e instanceof ClassroomApiError && e.code === "revision_conflict") {
        setConflict(true);
      } else {
        setNotice(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, activeJob, workspaceId, lessonId, baseRevision, onReload]);

  // 单页重生成：创建后交给 GenerationProgress 展示
  const startRegenerate = async (instruction: string, blockId?: string) => {
    if (!current || busy || activeJob) return;
    setBusy("regen");
    try {
      const res = await createRevision(workspaceId, lessonId, {
        base_revision: baseRevision,
        operation: blockId
          ? { op: "regenerate_block", slide_id: current.slide_id, block_id: blockId, instruction }
          : { op: "regenerate_slide", slide_id: current.slide_id, instruction },
      }, editKey());
      const job = await getJob(workspaceId, lessonId, res.job_id);
      setRegenJob(job);
    } catch (e) {
      if (e instanceof ClassroomApiError && e.code === "revision_conflict") {
        setConflict(true);
      } else {
        setNotice(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(null);
    }
  };

  const retryLatestJob = async () => {
    const job = detail.latest_job;
    if (!job || busy) return;
    setBusy("retry");
    setNotice(null);
    try {
      const next = await retryJob(workspaceId, lessonId, job.job_id,
        { expected_state_revision: job.state_revision });
      setRegenJob(next);
      setMobilePanel("script");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : tr("cls.progress.action.error"));
      onReload();
    } finally {
      setBusy(null);
    }
  };

  const doSearchImages = async () => {
    if (!current || busy || activeJob) return;
    setBusy("images");
    setNotice(null);
    try {
      const res = await imageSearch(workspaceId, {
        lesson_id: lessonId,
        visual_intent: {
          role: "scene",
          purpose: current.title,
          query_terms: current.title.split(/[：:，,]/).slice(0, 3),
          orientation: "landscape",
        },
      }, editKey());
      setCandidates(res.candidates ?? []);
      if (!res.candidates?.length) setNotice(tr("cls.img.empty"));
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const doReplaceImage = async (candidateId: string) => {
    if (!current) return;
    const imageBlock = current.blocks.find(
      (b): b is ImageBlock => b.kind === "image") as ImageBlock | undefined;
    if (!imageBlock) return;
    setCandidates(null);
    await runOperation({
      op: "replace_image",
      slide_id: current.slide_id,
      block_id: imageBlock.id,
      candidate_id: candidateId,
    }, "replaceimg");
  };

  const moveSlide = async (dir: -1 | 1) => {
    if (!current || busy || activeJob) return;
    const ids = slides.map((s) => s.slide_id);
    const i = page;
    const j = i + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    await runOperation({ op: "edit_content", changes: [
      { op: "reorder_slides", page_ids: ids }] }, "reorder");
  };

  const deleteSlide = async () => {
    if (!current || slides.length <= 1 || busy) return;
    await runOperation({ op: "edit_content", changes: [
      { op: "delete_slide", slide_id: current.slide_id }] }, "delete");
  };

  if (!revision || !current) return null;

  return (
    <div className="lesson-editor flex h-full min-h-0 flex-col">
      {conflict && (
        <div className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-5 py-2 text-[0.73rem] text-amber-800 dark:text-amber-300">
          <AlertTriangle size={13} />
          <span className="flex-1">{tr("cls.edit.conflict")}</span>
          <Button size="sm" variant="outline" icon={<RefreshCw size={12} />} onClick={() => onReload()}>
            {tr("cls.edit.reload")}
          </Button>
        </div>
      )}
      {notice && (
        <div className="flex items-center gap-2 border-b border-border bg-surface-hover px-5 py-2 text-[0.72rem] text-fg-secondary">
          <span className="flex-1">{notice}</span>
          <button onClick={() => setNotice(null)} className="cursor-pointer text-muted hover:text-fg"><X size={12} /></button>
        </div>
      )}
      {!regenJob && detail.latest_job &&
        ["failed", "cancelled"].includes(detail.latest_job.state) &&
        detail.latest_job.next_actions?.includes("retry") && (
          <div className="flex flex-wrap items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-5 py-2 text-[0.72rem] text-amber-800 dark:text-amber-300">
            <AlertTriangle size={13} />
            <span className="min-w-0 flex-1">{tr("cls.edit.failed")}{detail.latest_job.last_error ? `：${detail.latest_job.last_error.slice(0, 120)}` : ""}</span>
            <Button size="sm" variant="outline" disabled={Boolean(busy)}
              icon={busy === "retry" ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              onClick={() => void retryLatestJob()}>{tr("cls.progress.retry")}</Button>
          </div>
      )}
      {detail.latest_job?.state === "succeeded" && Boolean(detail.latest_job.warnings?.length) && (
        <details className="shrink-0 border-b border-border bg-surface-hover px-5 py-2 text-[0.72rem] text-fg-secondary">
          <summary className="cursor-pointer">{tr("cls.edit.generationNotes")}</summary>
          <ul className="mt-2 max-h-36 list-disc space-y-1 overflow-y-auto pl-4">
            {detail.latest_job.warnings!.map((warning, index) => <li key={index}>{warning}</li>)}
          </ul>
        </details>
      )}

      <div className="editor-compact-tools flex shrink-0 items-center gap-1 border-b border-border bg-surface px-2 py-1">
        <button type="button" onClick={() => setMobilePanel("outline")}
                className="classroom-header-action" aria-label={tr("cls.edit.outline")}>
          <List size={18} />
        </button>
        {(["content", "script", "sources", "settings"] as Tab[]).map((entry) => (
          <button key={entry} type="button"
                  onClick={() => { setTab(entry); setMobilePanel(entry); }}
                  className="min-h-11 rounded-lg px-3 text-sm text-fg-secondary hover:bg-surface-hover">
            {tabLabel(entry)}
          </button>
        ))}
      </div>
      <div className="relative flex min-h-0 flex-1">
        {/* 左：缩略目录 */}
        <nav aria-label={tr("cls.edit.outline")}
             className={cn("editor-outline w-44 shrink-0 flex-col gap-2 overflow-y-auto border-r border-border-light bg-surface p-3",
               mobilePanel === "outline"
                 ? "motion-drawer absolute inset-y-0 left-0 z-20 flex w-[min(340px,100%)] shadow-xl"
                 : "hidden")}>
          {mobilePanel === "outline" && (
            <div className="flex items-center justify-between border-b border-border px-3 py-2 editor-panel-dismiss">
              <strong className="text-sm">{tr("cls.edit.outline")}</strong>
              <button type="button" className="classroom-header-action"
                      aria-label={tr("cls.edit.close.outline")}
                      onClick={() => setMobilePanel(null)}><X size={18} /></button>
            </div>
          )}
          {slides.map((s, i) => (
            <button
              key={s.slide_id}
              onClick={() => { setPage(i); frameRef.current?.gotoPage(i + 1); setMobilePanel(null); }}
              aria-current={i === page ? "true" : undefined}
              className={cn(
                "flex cursor-pointer flex-col gap-2 rounded-xl border p-1.5 text-left text-xs transition-colors",
                i === page
                  ? "border-accent/50 bg-accent-soft/40 font-medium text-accent-strong"
                  : "border-transparent text-fg-secondary hover:bg-surface-hover hover:text-fg",
              )}
            >
              <SlideThumbnail slide={s} themeId={revision.brief.theme_id} />
              <span className="flex w-full items-center gap-2 px-1 pb-1"><span className="tnum text-[10px] text-muted">{s.order}</span><span className="truncate text-[11px]">{s.title}</span></span>
            </button>
          ))}
        </nav>

        {/* 中：课件舞台 */}
        <div className="editor-stage flex min-w-0 flex-1 flex-col items-center justify-center gap-4 p-3 sm:p-6">
          <SlideFrame
            ref={frameRef}
            html={frameHtml}
            title={detail.title}
            onPageSelected={(order) => setPage(order - 1)}
            className="editor-slide-frame h-full min-h-0 w-full overflow-hidden rounded-xl border border-border/40 shadow-lg"
          />
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" icon={<ChevronLeft size={14} />}
              disabled={page === 0}
              onClick={() => { setPage(page - 1); frameRef.current?.gotoPage(page); }}>
              {tr("cls.edit.prev")}
            </Button>
            <span className="tnum text-[0.72rem] text-muted">
              {page + 1} / {slides.length}
            </span>
            <Button size="sm" variant="ghost" disabled={page >= slides.length - 1}
              onClick={() => { setPage(page + 1); frameRef.current?.gotoPage(page + 2); }}>
              {tr("cls.edit.next")}<ChevronRight size={14} />
            </Button>
          </div>
        </div>

        {/* 右：讲稿 | 来源 | 设置 */}
        <aside className={cn(
          "editor-inspector w-80 shrink-0 flex-col border-l border-border-light bg-surface",
          mobilePanel && mobilePanel !== "outline"
            ? "motion-drawer absolute inset-y-0 right-0 z-20 flex w-[min(420px,100%)] shadow-xl"
            : "hidden",
        )}>
          {mobilePanel && mobilePanel !== "outline" && (
            <div className="flex items-center justify-between border-b border-border px-3 py-1 editor-panel-dismiss">
              <span className="text-sm font-semibold text-fg">{tabLabel(tab)}</span>
              <button type="button" className="classroom-header-action"
                      aria-label={tr("cls.edit.close.panel")}
                      onClick={() => setMobilePanel(null)}><X size={18} /></button>
            </div>
          )}
          <div className="flex shrink-0 border-b border-border" role="tablist">
            {(["content", "script", "sources", "settings"] as Tab[]).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                onClick={() => { setTab(t); frameRef.current?.setBlockState(["*"], []); }}
                className={cn(
                  "flex-1 cursor-pointer border-b-2 px-3 py-4 text-xs font-medium transition-colors",
                  tab === t
                    ? "border-accent text-accent-strong"
                    : "border-transparent text-muted hover:bg-surface-hover/60 hover:text-fg-secondary",
                )}
              >
                {tabLabel(t)}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            {activeJob ? (
              <div className="flex flex-col gap-3">
                <p className="text-[0.75rem] font-medium text-fg">{lang === "en" ? "Updating courseware" : "正在更新课件"}</p>
                {["failed", "cancelled"].includes(activeJob.state) && (
                  <Button size="sm" variant="ghost" onClick={() => { setRegenJob(null); onReload(); }}>
                    {tr("cls.edit.back")}
                  </Button>
                )}
                <GenerationProgress
                  workspaceId={workspaceId} lessonId={lessonId} job={activeJob}
                  onUpdated={(job) => {
                    if (job.state === "succeeded") {
                      setRegenJob(null);
                      onReload();
                    } else if (["failed", "cancelled"].includes(job.state)) {
                      setRegenJob(job);
                    }
                  }}
                />
              </div>
            ) : tab === "content" ? (
              <BlockEditor key={`${baseRevision}:${current.slide_id}`} slide={current} busy={locked}
                onSelect={(id) => frameRef.current?.setBlockState(["*"], [id])}
                onDirtyChange={setBlockDirty}
                onSave={(block) => void runOperation({ op: "edit_content", changes: [
                  { op: "replace_block", slide_id: current.slide_id, block_id: block.id, block }] }, "block")}
                onOptimize={(id, instruction) => void startRegenerate(instruction, id)} />
            ) : tab === "script" ? (
              <ScriptTab key={`${baseRevision}:${current.slide_id}`}
                slide={current}
                editing={editing}
                onEditToggle={() => setEditing((v) => !v)}
                busy={locked}
                onDirtyChange={setScriptDirty}
                onSave={(updated) => {
                  setEditing(false);
                  void runOperation({ op: "edit_content", changes: [
                    { op: "replace_slide", slide_id: current.slide_id, slide: updated }] },
                    "edit");
                }}
              />
            ) : tab === "sources" ? (
              <SourcesTab revision={revision} slide={current} />
            ) : (
              <SettingsTab
                revision={revision}
                busy={locked}
                onTheme={(themeId) => void runOperation({
                  op: "change_theme", theme_id: themeId as ChangeThemeOperation["theme_id"] }, "theme")}
                onRegen={(instruction) => void startRegenerate(instruction)}
                onImages={() => void doSearchImages()}
                candidates={candidates}
                onPickCandidate={(cid) => void doReplaceImage(cid)}
                hasImageBlock={current.blocks.some((b) => b.kind === "image")}
                onMove={(dir) => void moveSlide(dir)}
                onDelete={() => void deleteSlide()}
                canDelete={slides.length > 1}
                page={page}
              />
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function ScriptTab({ slide, editing, onEditToggle, busy, onSave, onDirtyChange }: {
  slide: SlideSpec;
  editing: boolean;
  onEditToggle: () => void;
  busy: boolean;
  onSave: (updated: SlideSpec) => void;
  /** 未保存修改信号（助手导航保护 §5.4）；切换页面时复位。 */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const [title, setTitle] = useState(slide.title);
  const [bullets, setBullets] = useState<(string | null)[]>(() => {
    const b = slide.blocks.find((x): x is BulletsBlock => x.kind === "bullets");
    return b ? b.items.map((it) => spansToText(it as never) ?? null) : [];
  });
  const [caption, setCaption] = useState(() => {
    const b = slide.blocks.find((x): x is ImageBlock => x.kind === "image");
    return b?.caption ?? "";
  });
  const [displaySegments, setDisplaySegments] = useState<string[]>(
    () => slide.segments.map((s) => s.display_text));
  const [spokenSegments, setSpokenSegments] = useState<string[]>(
    () => slide.segments.map((s) => s.spoken_text));
  const [dirty, setDirty] = useState(false);
  // 未保存信号上抛；切页/保存后重载由 resetFor 分支或组件重挂复位。
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);
  // 切页时重置编辑缓冲（渲染期派生，跟随 slide 标识）。
  const [resetFor, setResetFor] = useState(slide.slide_id);
  if (resetFor !== slide.slide_id) {
    setResetFor(slide.slide_id);
    setTitle(slide.title);
    const b = slide.blocks.find((x): x is BulletsBlock => x.kind === "bullets");
    setBullets(b ? b.items.map((it) => spansToText(it as never) ?? null) : []);
    const img = slide.blocks.find((x): x is ImageBlock => x.kind === "image");
    setCaption(img?.caption ?? "");
    setDisplaySegments(slide.segments.map((s) => s.display_text));
    setSpokenSegments(slide.segments.map((s) => s.spoken_text));
    setDirty(false);
  }

  const save = () => {
    const next = JSON.parse(JSON.stringify(slide)) as SlideSpec;
    next.title = title.trim() || slide.title;
    const b = next.blocks.find((x): x is BulletsBlock => x.kind === "bullets");
    if (b) {
      b.items = b.items.map((item, i) => {
        const t = bullets[i];
        // 只回写纯文本要点；含公式/强调的保持原样
        return t != null && t.trim() ? [{ kind: "text", text: t.trim() } as never] : item;
      });
    }
    const img = next.blocks.find((x): x is ImageBlock => x.kind === "image");
    if (img && caption.trim()) img.caption = caption.trim();
    next.segments = next.segments.map((seg, i) => ({
      ...seg,
      display_text: (displaySegments[i] ?? seg.display_text).trim() || seg.display_text,
      spoken_text: (spokenSegments[i] ?? seg.spoken_text).trim() || seg.spoken_text,
    }));
    onSave(next);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-[0.75rem] font-medium text-fg">{tr("cls.script.title")}</p>
        <button
          onClick={() => { if (!editing) setDirty(false); onEditToggle(); }}
          disabled={busy}
          className="flex cursor-pointer items-center gap-1 text-[0.7rem] text-accent-strong hover:underline disabled:opacity-50"
        >
          <Pencil size={11} />
          {editing ? tr("cls.script.cancel.edit") : tr("cls.script.edit")}
        </button>
      </div>
      {editing ? (
        <>
          <Field label={tr("cls.script.field.title")}>
            <Input value={title} maxLength={40} onChange={(e) => { setTitle(e.target.value); setDirty(true); }} />
          </Field>
          {bullets.length > 0 && (
            <Field label={tr("cls.script.field.bullets")}>
              {bullets.map((t, i) => (
                t == null ? (
                  <p key={i} className="mb-1.5 rounded-[6px] bg-surface-hover px-2 py-1.5 text-[0.66rem] text-muted">
                    {tr("cls.script.rich.bullet")}
                  </p>
                ) : (
                  <Input key={i} value={t} className="mb-1.5" maxLength={120}
                    onChange={(e) => { setBullets(bullets.map((x, j) => j === i ? e.target.value : x)); setDirty(true); }} />
                )
              ))}
            </Field>
          )}
          {caption !== "" && (
            <Field label={tr("cls.script.field.caption")}>
              <Input value={caption} maxLength={300}
                onChange={(e) => { setCaption(e.target.value); setDirty(true); }} />
            </Field>
          )}
          <Field label={tr("cls.script.field.segments")}>
            {displaySegments.map((t, i) => (
              <div key={i} className="mb-3 rounded-lg border border-border p-2">
                <p className="mb-1 text-xs text-muted">{tr("cls.script.field.segment.slide").replace("%n", String(i + 1))}</p>
                <Textarea rows={2} value={t}
                  onChange={(e) => { setDisplaySegments(displaySegments.map((x, j) => j === i ? e.target.value : x)); setDirty(true); }} />
                <p className="mb-1 mt-2 text-xs text-muted">{tr("cls.script.field.segment.spoken")}</p>
                <Textarea rows={3} value={spokenSegments[i] ?? ""}
                  onChange={(e) => { setSpokenSegments(spokenSegments.map((x, j) => j === i ? e.target.value : x)); setDirty(true); }} />
              </div>
            ))}
          </Field>
          <Button demoWrite size="sm" disabled={!dirty || busy}
            icon={busy ? <Loader2 size={12} className="animate-spin" /> : undefined}
            onClick={save}>
            {tr("cls.script.save")}
          </Button>
          <p className="text-[0.65rem] leading-relaxed text-muted/80">{tr("cls.script.save.hint")}</p>
        </>
      ) : (
        <>
          <p className="text-[0.8rem] font-medium text-fg">{slide.title}</p>
          <ol className="flex flex-col gap-3">
            {slide.segments.map((seg) => (
              <li key={seg.segment_id} className="rounded-xl border border-border-light bg-bg/50 p-3.5">
                <span className="mb-2 inline-flex rounded-full bg-accent-soft/60 px-2 py-1 text-[10px] text-accent-strong">
                  {tr(`cls.role.${seg.role}`, seg.role)}
                </span>
                <Markdown className="chat-prose classroom-prose">{seg.display_text || seg.spoken_text}</Markdown>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

function SourcesTab({ revision, slide }: {
  revision: RevisionPublic; slide: SlideSpec;
}) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const ids = new Set<string>([
    ...(slide.source_ids ?? []),
    ...(slide.claims ?? []).flatMap((c) => c.source_ids ?? []),
    ...slide.segments.flatMap((seg) => seg.source_ids ?? []),
  ]);
  const records = revision.source_records.filter((r) => ids.has(r.source_id));
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[0.75rem] font-medium text-fg">{tr("cls.sources.title")}</p>
      {records.length === 0 && (
        <p className="text-[0.72rem] text-muted">{tr("cls.sources.none")}</p>
      )}
      {records.map((r) => (
        <div key={r.source_id} className="rounded-[8px] border border-border bg-surface p-2.5">
          <p className="flex items-center gap-1.5 text-[0.73rem] font-medium text-fg">
            <span className={cn(
              "rounded-[4px] px-1 py-0.5 text-[0.6rem]",
              r.kind === "textbook" || r.kind === "workspace_file"
                ? "bg-accent-soft text-accent-strong"
                : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
            )}>
              {r.kind === "web" ? tr("cls.sources.web") : tr("cls.sources.book")}
            </span>
            <span className="truncate">{r.title}</span>
          </p>
          <p className="mt-1 flex flex-wrap gap-x-2 text-[0.65rem] text-muted">
            {(r.section_path ?? []).length > 0 && <span>{(r.section_path ?? []).join(" / ")}</span>}
            {r.printed_page && <span className="tnum">p.{r.printed_page}</span>}
            {r.url && (
              <a href={r.url} target="_blank" rel="noreferrer"
                className="text-accent-strong hover:underline">{r.domain ?? r.url}</a>
            )}
          </p>
        </div>
      ))}
    </div>
  );
}

function SettingsTab({ revision, busy, onTheme, onRegen, onImages,
  candidates, onPickCandidate, hasImageBlock, onMove, onDelete, canDelete,
  page }: {
  revision: RevisionPublic;
  busy: boolean;
  onTheme: (themeId: string) => void;
  onRegen: (instruction: string) => void;
  onImages: () => void;
  candidates: ImageCandidate[] | null;
  onPickCandidate: (candidateId: string) => void;
  hasImageBlock: boolean;
  onMove: (dir: -1 | 1) => void;
  onDelete: () => void;
  canDelete: boolean;
  page: number;
}) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const [custom, setCustom] = useState("");
  return (
    <div className="flex flex-col gap-4">
      {/* 单页重生成（LLM） */}
      <div>
        <p className="mb-1.5 text-[0.73rem] font-medium text-fg">{tr("cls.regen.title")}</p>
        <div className="flex flex-wrap gap-1.5">
          {REGEN_PRESETS.map((p) => (
            <button key={p.key} type="button" disabled={busy}
              onClick={() => onRegen(tr(p.instrKey))}
              className="cursor-pointer rounded-full border border-border px-2.5 py-1 text-[0.68rem] text-fg-secondary transition-colors hover:border-accent/50 hover:text-accent-strong disabled:opacity-50">
              {tr(p.key)}
            </button>
          ))}
        </div>
        <div className="mt-2 flex gap-1.5">
          <Input value={custom} maxLength={500} placeholder={tr("cls.regen.custom.ph")}
            onChange={(e) => setCustom(e.target.value)} />
          <Button demoWrite size="sm" variant="outline" icon={<Sparkles size={12} />}
            disabled={busy || !custom.trim()} onClick={() => { onRegen(custom.trim()); setCustom(""); }}>
            {tr("cls.regen.go")}
          </Button>
        </div>
      </div>

      {/* 换视觉风格（零 LLM 确定性重编译） */}
      <div>
        <p className="mb-1.5 text-[0.73rem] font-medium text-fg">{tr("cls.edit.theme")}</p>
        <ThemeSwitcher value={revision.brief.theme_id} disabled={busy} onPick={onTheme} />
        <p className="mt-1 text-[0.64rem] text-muted/80">{tr("cls.edit.theme.hint")}</p>
      </div>

      {/* 换图 */}
      <div>
        <p className="mb-1.5 text-[0.73rem] font-medium text-fg">{tr("cls.img.title")}</p>
        {!hasImageBlock ? (
          <p className="text-[0.68rem] text-muted">{tr("cls.img.noblock")}</p>
        ) : candidates === null ? (
          <Button size="sm" variant="outline" icon={<ImageIcon size={12} />} disabled={busy} onClick={onImages}>
            {tr("cls.img.search")}
          </Button>
        ) : candidates.length === 0 ? (
          <p className="text-[0.68rem] text-muted">{tr("cls.img.empty")}</p>
        ) : (
          <div className="grid grid-cols-2 gap-1.5">
            {candidates.map((c) => (
              <button key={c.candidate_id} type="button" disabled={busy}
                onClick={() => onPickCandidate(c.candidate_id)}
                className="cursor-pointer overflow-hidden rounded-[8px] border border-border transition-colors hover:border-accent disabled:opacity-50">
                {c.thumbnail_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={c.thumbnail_url} alt={c.creator ?? "candidate"}
                    className="h-16 w-full object-cover" loading="lazy" />
                ) : (
                  <div className="flex h-16 items-center justify-center bg-surface-hover text-[0.6rem] text-muted">
                    {c.provider}
                  </div>
                )}
                <p className="truncate px-1.5 py-1 text-[0.6rem] text-muted">
                  {c.creator || c.provider}
                </p>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 页操作 */}
      <div>
        <p className="mb-1.5 text-[0.73rem] font-medium text-fg">{tr("cls.page.ops")}</p>
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" variant="outline" icon={<ArrowUp size={12} />}
            disabled={busy || page === 0} onClick={() => onMove(-1)}>
            {tr("cls.page.up")}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => onMove(1)}>
            <ArrowDown size={12} />{tr("cls.page.down")}
          </Button>
          <Button demoWrite size="sm" variant="danger" icon={<Trash2 size={12} />}
            disabled={busy || !canDelete} onClick={onDelete}>
            {tr("cls.page.delete")}
          </Button>
        </div>
        {!canDelete && (
          <p className="mt-1 text-[0.62rem] text-muted/70">{tr("cls.page.last")}</p>
        )}
      </div>
    </div>
  );
}

/** 换主题：复用模板缩略图（数据来自 GET /classroom/templates）。 */
function ThemeSwitcher({ value, disabled, onPick }: {
  value: string; disabled: boolean; onPick: (id: string) => void;
}) {
  const { lang } = useUIStore();
  const [themes, setThemes] = useState<ThemeTemplateInfo[]>([]);
  useEffect(() => {
    let cancelled = false;
    import("@/lib/api-classroom").then(({ getClassroomTemplates }) =>
      getClassroomTemplates(lang === "en" ? "en" : "zh")
        .then((t) => { if (!cancelled) setThemes(t.themes ?? []); })
        .catch(() => undefined));
    return () => { cancelled = true; };
  }, [lang]);
  const compatibleThemes = value.endsWith("@1")
    ? themes.map((theme) => ({ ...theme,
        theme_id: theme.theme_id.replace("@2", "@1"), version: "1" }))
    : themes;
  return (
    <TemplatePicker themes={compatibleThemes} value={value}
      onChange={(id) => { if (!disabled && id !== value) onPick(id); }} />
  );
}

/** 打印桥脚本：注入后端课程 HTML 尾部（沙箱 iframe 内执行）。观察
 * data-print-ready/-error 并自驱动 window.print()——沙箱内是 opaque
 * origin，父窗口无法跨源调用 contentWindow.print()；错误经 postMessage
 * 回传给外壳再中继回应用侧展示。 */
const PRINT_BRIDGE_SCRIPT = `<script>(function(){
var done=false;
function send(m){try{parent.postMessage(m,"*")}catch(e){}}
function check(){
 if(done)return true;
 var ds=document.documentElement.dataset;
 if(ds.printError){done=true;send({eduPrint:"error",error:String(ds.printError)});return true}
 if(ds.printReady==="1"){done=true;send({eduPrint:"ready"});try{window.focus();window.print()}catch(e){send({eduPrint:"error",error:String(e)})}return true}
 return false}
if(!check()){
 var iv=null,ticks=0;
 var mo=new MutationObserver(function(){if(check()){mo.disconnect();if(iv)clearInterval(iv)}});
 mo.observe(document.documentElement,{attributes:true,attributeFilter:["data-print-ready","data-print-error"]});
 iv=setInterval(function(){
  if(check()){clearInterval(iv);mo.disconnect();return}
  if(++ticks>120){clearInterval(iv);mo.disconnect();send({eduPrint:"error",error:"timeout"})}},250)}
})();</${"script"}>`;

/** 向打印弹窗写入受信外壳（常量模板，不含任何课程内容），课程 HTML 只
 * 允许进入 sandbox iframe（与播放器 SlideFrame 同级防护）。旧实现把课程
 * HTML document.write 进同源窗口：课程内容一旦夹带脚本即可读取应用
 * origin 的 localStorage token。 */
function writePrintShell(popup: Window, title: string): HTMLIFrameElement | null {
  const safeTitle = title.replace(/[&<>"']/g, "");
  popup.document.open();
  popup.document.write(
    `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${safeTitle}</title>` +
    `<style>html,body{margin:0;padding:0;height:100%;overflow:hidden;background:#fff}` +
    `iframe{border:0;display:block;width:100%;height:100%}</style></head><body>` +
    `<iframe id="edu-print-frame" title="${safeTitle}" sandbox="allow-scripts allow-modals"></iframe>` +
    `<script>(function(){var f=document.getElementById("edu-print-frame");` +
    `window.addEventListener("message",function(ev){if(ev.source!==f.contentWindow)return;` +
    `var d=ev.data||{};if(d.eduPrint==="error"){` +
    `try{if(window.opener)window.opener.postMessage({eduPrintRelay:true,error:d.error||""},window.location.origin)}catch(e){}` +
    `window.close()}})})();</${"script"}></body></html>`);
  popup.document.close();
  return popup.document.getElementById("edu-print-frame") as HTMLIFrameElement | null;
}

/** 下载入口（鉴权 fetch blob，页面头部渲染）。 */
export function ExportButtons({ workspaceId, lessonId, revision, tr }: {
  workspaceId: string; lessonId: string; revision: number;
  tr: (k: string, fb?: string) => string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const trRef = useRef(tr);
  useEffect(() => { trRef.current = tr; }, [tr]);
  useEffect(() => {
    // 打印沙箱的错误中继（popup 同源转发）：只接受本源消息与既定标记。
    const onRelay = (ev: MessageEvent) => {
      if (ev.origin !== window.location.origin) return;
      const data = ev.data as { eduPrintRelay?: boolean; error?: string } | null;
      if (!data || data.eduPrintRelay !== true) return;
      const err = data.error || "";
      setExportError(!err || err === "timeout"
        ? trRef.current("cls.export.print.failed")
        : trRef.current("cls.export.print.overflow").replace("%s", err));
    };
    window.addEventListener("message", onRelay);
    return () => window.removeEventListener("message", onRelay);
  }, []);
  const download = async (format: "html_zip" | "notes_md") => {
    if (busy) return;
    setBusy(format);
    setExportError(null);
    try {
      const { API_BASE } = await import("@/lib/api");
      const { apiFetch } = await import("@/lib/api-fetch");
      const res = await apiFetch(
        `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}` +
        `/classroom/lessons/${encodeURIComponent(lessonId)}/exports`, {
          method: "POST",
          headers: { "Content-Type": "application/json",
                     // Each explicit export gets its own request key.
                     "Idempotency-Key": "exp_" + Array.from(crypto.getRandomValues(new Uint8Array(12)), (byte) => byte.toString(16).padStart(2, "0")).join("") },
          body: JSON.stringify({ revision, format }),
        });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { content_url } = await res.json() as { content_url: string };
      const blobRes = await apiFetch(content_url);
      if (!blobRes.ok) throw new Error(`HTTP ${blobRes.status}`);
      const blob = await blobRes.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = format === "html_zip"
        ? `lesson-${lessonId}-r${revision}.zip`
        : `lesson-${lessonId}-r${revision}-notes.md`;
      a.click();
      // 延迟 revoke：立即回收会让浏览器来不及开始读取 blob 下载
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
    } catch {
      setExportError(tr("cls.export.failed"));
    }
    finally { setBusy(null); }
  };
  const printPdf = async () => {
    if (busy) return;
    // 用户点击时同步打开窗口，避免异步鉴权请求完成后被浏览器拦截。
    const popup = window.open("", "_blank");
    if (!popup) {
      setExportError(tr("cls.export.popup"));
      return;
    }
    setBusy("print");
    setExportError(null);
    try {
      // 先写受信外壳；课程 HTML 只进 sandbox iframe，就绪/错误经打印桥回传。
      const frame = writePrintShell(popup, tr("cls.export.print"));
      if (!frame) throw new Error("print shell unavailable");
      const { getRevisionFrame } = await import("@/lib/api-classroom");
      const html = await getRevisionFrame(workspaceId, lessonId, revision,
                                          "print");
      const bodyClose = html.toLowerCase().lastIndexOf("</body>");
      // srcdoc 属性赋值（非字符串拼进 HTML 属性），无需转义课程内容。
      frame.srcdoc = bodyClose === -1
        ? html + PRINT_BRIDGE_SCRIPT
        : html.slice(0, bodyClose) + PRINT_BRIDGE_SCRIPT + html.slice(bodyClose);
    } catch {
      popup.close();
      setExportError(tr("cls.export.print.failed"));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="relative">
      <Button size="sm" variant="outline" icon={busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} aria-expanded={exportOpen} onClick={() => { setExportError(null); setExportOpen((open) => !open); }}>{tr("cls.export.title")}<ChevronDown size={12} /></Button>
      {exportOpen && <>
        <div className="fixed inset-0 z-30" onClick={() => setExportOpen(false)} />
        <div className="motion-pop absolute right-0 top-full z-40 mt-2 w-48 rounded-xl border border-border bg-surface p-1.5 shadow-lg" onKeyDown={(e) => { if (e.key === "Escape") setExportOpen(false); }}>
          {(["html_zip", "notes_md", "print"] as const).map((format) => {
            const Icon = format === "print" ? Printer : format === "notes_md" ? FileText : Download;
            return <button key={format} type="button" disabled={Boolean(busy)} className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-xs text-fg-secondary hover:bg-surface-hover disabled:opacity-50" onClick={() => { setExportOpen(false); if (format === "print") void printPdf(); else void download(format); }}><Icon size={14} />{tr(format === "print" ? "cls.export.print" : format === "notes_md" ? "cls.export.notes" : "cls.export.zip")}</button>;
          })}
        </div>
      </>}
      {exportError && <span role="alert" className="absolute right-0 top-full z-40 mt-2 w-64 rounded-xl border border-danger/20 bg-surface p-3 text-xs text-danger shadow-lg">{exportError}</span>}
    </div>
  );
}
