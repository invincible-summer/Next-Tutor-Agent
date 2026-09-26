"use client";
/* 课程预览/编辑器（plan.md §4.3 / E04）。
 * 三栏：左缩略目录 | 中课件舞台 | 右「讲稿 | 来源 | 设置」。默认只打开
 * 当前页讲稿。所有修改以 base_revision 做 CAS；revision_conflict 显示
 * 「内容已更新」并允许重载。零 LLM 操作（换主题/排序/删页/换图/改文字）
 * 走快速修订；单页重生成（更详细/更简洁/换个例子）展示生成进度。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Download,
  Image as ImageIcon, Loader2, Pencil, RefreshCw, Sparkles, Trash2, X,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import {
  ClassroomApiError, createRevision, getJob, imageSearch,
} from "@/lib/api-classroom";
import type {
  BulletsBlock, ImageBlock, ImageCandidate, JobPublic, LessonDetailPublic,
  RevisionPublic, SlideSpec, ThemeTemplateInfo,
} from "@/lib/types-classroom.generated";
import { Button } from "@/components/ui/Button";
import { Input, Textarea, Field } from "@/components/ui/Input";
import SlideFrame, { type SlideFrameHandle } from "./SlideFrame";
import { GenerationProgress } from "./GenerationProgress";
import { TemplatePicker } from "./TemplatePicker";
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
  { key: "cls.regen.detail", instruction: "把本页内容讲得更详细一些，补充推导或例子的中间步骤。" },
  { key: "cls.regen.brief", instruction: "把本页内容精简一些，只保留最核心的要点。" },
  { key: "cls.regen.example", instruction: "为这一页换一个更贴近生活的具体例子。" },
] as const;

type Tab = "script" | "sources" | "settings";

export function LessonEditor({ workspaceId, lessonId, detail, frameHtml,
  onReload }: {
  workspaceId: string;
  lessonId: string;
  detail: LessonDetailPublic;
  /** 自包含课件 HTML（父页面经鉴权 fetch 取得）。 */
  frameHtml: string;
  /** 重载课程（修订完成后拉最新 revision）。 */
  onReload: (revision?: number) => void;
}) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const revision = detail.revision;
  const frameRef = useRef<SlideFrameHandle>(null);
  const [page, setPage] = useState(0); // 0 基
  const [tab, setTab] = useState<Tab>("script");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [regenJob, setRegenJob] = useState<JobPublic | null>(null);
  const [candidates, setCandidates] = useState<ImageCandidate[] | null>(null);
  const idemRef = useRef(0);

  const slides = useMemo(() => revision?.slides ?? [], [revision]);
  const current = slides[page];
  const baseRevision = revision?.revision ?? 1;

  // 页码越界（删除页后）回到末页：渲染期派生重置（React 官方模式）。
  const [lastCount, setLastCount] = useState(slides.length);
  if (slides.length !== lastCount) {
    setLastCount(slides.length);
    if (page > slides.length - 1) setPage(Math.max(0, slides.length - 1));
  }

  const nextIdem = () => {
    idemRef.current += 1;
    return `edit_${Date.now().toString(36)}_${idemRef.current}`;
  };

  /** 提交修订操作；零 LLM 快速路径完成后自动重载最新版本。 */
  const runOperation = useCallback(async (operation: Record<string, unknown>,
    label: string, reloadRevision?: number) => {
    if (busy) return;
    setBusy(label);
    setConflict(false);
    setNotice(null);
    try {
      const res = await createRevision(
        workspaceId, lessonId,
        { base_revision: reloadRevision ?? baseRevision,
          operation: operation as never },
        nextIdem());
      // 轮询到终态（快速修订通常 <2s；重生成走 GenerationProgress）
      for (let i = 0; i < 300; i++) {
        const job = await getJob(workspaceId, lessonId, res.job_id).catch(() => null);
        if (job && ["succeeded", "failed", "cancelled"].includes(job.state)) {
          if (job.state !== "succeeded") {
            setNotice(`${tr("cls.edit.failed")}${job.last_error ? `：${job.last_error.slice(0, 120)}` : ""}`);
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
  }, [busy, workspaceId, lessonId, baseRevision, onReload]);

  // 单页重生成：创建后交给 GenerationProgress 展示
  const startRegenerate = async (instruction: string) => {
    if (!current || busy) return;
    setBusy("regen");
    try {
      const res = await createRevision(workspaceId, lessonId, {
        base_revision: baseRevision,
        operation: {
          op: "regenerate_slide",
          slide_id: current.slide_id,
          instruction,
        },
      }, nextIdem());
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

  const doSearchImages = async () => {
    if (!current || busy) return;
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
      }, nextIdem());
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
    if (!current || busy) return;
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
    <div className="flex h-full min-h-0 flex-col">
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

      <div className="flex min-h-0 flex-1">
        {/* 左：缩略目录 */}
        <nav aria-label={tr("cls.edit.outline")} className="hidden w-48 shrink-0 flex-col overflow-y-auto border-r border-border py-2 md:flex">
          {slides.map((s, i) => (
            <button
              key={s.slide_id}
              onClick={() => { setPage(i); frameRef.current?.gotoPage(i + 1); }}
              aria-current={i === page ? "true" : undefined}
              className={cn(
                "flex cursor-pointer items-baseline gap-2 px-3 py-1.5 text-left text-[0.73rem] transition-colors",
                i === page
                  ? "bg-accent-soft/40 font-medium text-accent-strong"
                  : "text-fg-secondary hover:bg-surface-hover",
              )}
            >
              <span className="tnum w-4 shrink-0 text-muted">{s.order}</span>
              <span className="truncate">{s.title}</span>
            </button>
          ))}
        </nav>

        {/* 中：课件舞台 */}
        <div className="flex min-w-0 flex-1 flex-col items-center justify-center gap-2 p-4">
          <SlideFrame
            ref={frameRef}
            html={frameHtml}
            title={detail.title}
            onPageSelected={(order) => setPage(order - 1)}
            className="aspect-video max-h-full w-full max-w-[860px] overflow-hidden rounded-[12px] border border-border bg-white shadow-md"
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
        <aside className="hidden w-80 shrink-0 flex-col border-l border-border lg:flex">
          <div className="flex shrink-0 border-b border-border" role="tablist">
            {(["script", "sources", "settings"] as Tab[]).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={cn(
                  "flex-1 cursor-pointer px-3 py-2 text-[0.73rem] font-medium transition-colors",
                  tab === t
                    ? "border-b-2 border-accent text-accent-strong"
                    : "text-muted hover:text-fg-secondary",
                )}
              >
                {tr(`cls.tab.${t}`)}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {regenJob ? (
              <div className="flex flex-col gap-3">
                <p className="text-[0.75rem] font-medium text-fg">{tr("cls.regen.title")}</p>
                <GenerationProgress
                  workspaceId={workspaceId} lessonId={lessonId} job={regenJob}
                  onUpdated={(job) => {
                    if (["succeeded", "failed", "cancelled"].includes(job.state)) {
                      setRegenJob(null);
                      if (job.state === "succeeded") onReload();
                    }
                  }}
                />
              </div>
            ) : tab === "script" ? (
              <ScriptTab
                slide={current}
                editing={editing}
                onEditToggle={() => setEditing((v) => !v)}
                busy={Boolean(busy)}
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
                busy={Boolean(busy)}
                onTheme={(themeId) => void runOperation({
                  op: "change_theme", theme_id: themeId }, "theme")}
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

function ScriptTab({ slide, editing, onEditToggle, busy, onSave }: {
  slide: SlideSpec;
  editing: boolean;
  onEditToggle: () => void;
  busy: boolean;
  onSave: (updated: SlideSpec) => void;
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
  const [segments, setSegments] = useState<string[]>(
    () => slide.segments.map((s) => s.display_text));
  const [dirty, setDirty] = useState(false);
  // 切页时重置编辑缓冲（渲染期派生，跟随 slide 标识）。
  const [resetFor, setResetFor] = useState(slide.slide_id);
  if (resetFor !== slide.slide_id) {
    setResetFor(slide.slide_id);
    setTitle(slide.title);
    const b = slide.blocks.find((x): x is BulletsBlock => x.kind === "bullets");
    setBullets(b ? b.items.map((it) => spansToText(it as never) ?? null) : []);
    const img = slide.blocks.find((x): x is ImageBlock => x.kind === "image");
    setCaption(img?.caption ?? "");
    setSegments(slide.segments.map((s) => s.display_text));
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
      display_text: (segments[i] ?? seg.display_text).trim() || seg.display_text,
      spoken_text: (segments[i] ?? seg.spoken_text).trim() || seg.spoken_text,
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
            {segments.map((t, i) => (
              <Textarea key={i} rows={2} value={t} className="mb-1.5"
                onChange={(e) => { setSegments(segments.map((x, j) => j === i ? e.target.value : x)); setDirty(true); }} />
            ))}
          </Field>
          <Button size="sm" disabled={!dirty || busy}
            icon={busy ? <Loader2 size={12} className="animate-spin" /> : undefined}
            onClick={save}>
            {tr("cls.script.save")}
          </Button>
          <p className="text-[0.65rem] leading-relaxed text-muted/80">{tr("cls.script.save.hint")}</p>
        </>
      ) : (
        <>
          <p className="text-[0.8rem] font-medium text-fg">{slide.title}</p>
          <ol className="flex flex-col gap-2 border-l border-border-light pl-3">
            {slide.segments.map((seg) => (
              <li key={seg.segment_id} className="text-[0.74rem] leading-relaxed text-fg-secondary">
                <span className="mr-1.5 rounded-[4px] bg-surface-hover px-1 py-0.5 text-[0.6rem] text-muted">
                  {tr(`cls.role.${seg.role}`, seg.role)}
                </span>
                {seg.display_text}
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
              onClick={() => onRegen(p.instruction)}
              className="cursor-pointer rounded-full border border-border px-2.5 py-1 text-[0.68rem] text-fg-secondary transition-colors hover:border-accent/50 hover:text-accent-strong disabled:opacity-50">
              {tr(p.key)}
            </button>
          ))}
        </div>
        <div className="mt-2 flex gap-1.5">
          <Input value={custom} maxLength={500} placeholder={tr("cls.regen.custom.ph")}
            onChange={(e) => setCustom(e.target.value)} />
          <Button size="sm" variant="outline" icon={<Sparkles size={12} />}
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
          <Button size="sm" variant="danger" icon={<Trash2 size={12} />}
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
  return (
    <TemplatePicker themes={themes} value={value}
      onChange={(id) => { if (!disabled && id !== value) onPick(id); }} />
  );
}

/** 下载入口（鉴权 fetch blob，页面头部渲染）。 */
export function ExportButtons({ workspaceId, lessonId, revision, tr }: {
  workspaceId: string; lessonId: string; revision: number;
  tr: (k: string, fb?: string) => string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const download = async (format: "html_zip" | "notes_md") => {
    if (busy) return;
    setBusy(format);
    try {
      const { API_BASE } = await import("@/lib/api");
      const { apiFetch } = await import("@/lib/api-fetch");
      const res = await apiFetch(
        `${API_BASE}/workspaces/${encodeURIComponent(workspaceId)}` +
        `/classroom/lessons/${encodeURIComponent(lessonId)}/exports`, {
          method: "POST",
          headers: { "Content-Type": "application/json",
                     // API 幂等键下限 16 字符：时间戳+随机后缀
                     "Idempotency-Key": `exp_${Date.now().toString(36)}` +
                       `_${Math.random().toString(36).slice(2, 10)}` },
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
    } catch { /* 静默：按钮态提示 */ }
    finally { setBusy(null); }
  };
  return (
    <div className="flex items-center gap-1.5">
      <Button size="sm" variant="ghost" icon={busy === "html_zip"
        ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
        disabled={Boolean(busy)} onClick={() => void download("html_zip")}>
        {tr("cls.export.zip")}
      </Button>
      <Button size="sm" variant="ghost" icon={busy === "notes_md"
        ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
        disabled={Boolean(busy)} onClick={() => void download("notes_md")}>
        {tr("cls.export.notes")}
      </Button>
    </div>
  );
}
