"use client";
/* 一键备课 Modal（plan.md §4.1，E02）。
 * 桌面 ~880px、窄屏全屏；顶部固定标题、底部固定操作、中间独立滚动。
 * 默认只展示 主题/来源/时长/教学模板/视觉模板；「更多设置」折叠其余。
 * 主题必填 2–120 字；选章节自动填主题（可改写）；提交带 Idempotency-Key。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen, ChevronDown, ChevronRight, FileText, Loader2, MessageSquareText,
  Plus, X,
} from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import {
  getTextbook, getTextbooks, getWorkspace, listSessions,
  type TextbookListItem, type TextbookOutlineChapter,
} from "@/lib/api";
import {
  ClassroomApiError, createLesson, getClassroomCapabilities,
  getClassroomTemplates,
} from "@/lib/api-classroom";
import type {
  ClassroomCapabilities, ClassroomTemplates, LessonBrief,
} from "@/lib/types-classroom.generated";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Field, Input, Textarea, FIELD_CLS } from "@/components/ui/Input";
import { TemplatePicker } from "./TemplatePicker";
import { STRINGS } from "./strings";
import { GRADE_LABELS } from "@/lib/i18n";

const DURATIONS = [5, 10, 15, 20, 30] as const;
const PAGE_PLANS: { value: NonNullable<LessonBrief["page_plan"]>; n: number }[] = [
  { value: "4_6", n: 5 }, { value: "6_9", n: 8 }, { value: "8_12", n: 10 },
  { value: "10_15", n: 12 }, { value: "14_20", n: 17 },
];
const TIMELINESS = ["basic", "recent_year", "recent_month"] as const;
const VOICE_MODES = ["auto", "cloud", "local", "silent"] as const;

interface FileEntry {
  /** SourceSelection.files[].file_id（授权面内的 file_id）。 */
  fileId: string;
  filename: string;
  isTextbook: boolean;
  textbookId?: string;
  selected: boolean;
  chapters: { title: string }[];
  outline: TextbookOutlineChapter[] | null;
  outlineLoading: boolean;
}

function Select({ value, onChange, options, className, disabled }: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  className?: string;
  disabled?: boolean;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={cn(FIELD_CLS, "cursor-pointer", className)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

export function CreateLessonModal({ open, onClose, workspaceId, workspaceName,
  initialTopic, onCreated }: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  workspaceName?: string;
  /** 空态双路径/深链预填的主题。 */
  initialTopic?: string;
  /** 创建成功：跳转课程详情页（进度在详情页展示，E03 增强）。 */
  onCreated: (lessonId: string) => void;
}) {
  const { lang, grade, outputLanguage } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const [templates, setTemplates] = useState<ClassroomTemplates | null>(null);
  const [capabilities, setCapabilities] = useState<ClassroomCapabilities | null>(null);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [wsSessions, setWsSessions] = useState<{ session_id: string; title: string }[]>([]);

  // 表单状态（默认值 §4.1；templates.defaults 到达后补齐）。挂载式使用：
  // 父组件仅在打开时渲染本 Modal，每次打开都是全新状态。
  const [topic, setTopic] = useState(initialTopic ?? "");
  const [topicDirty, setTopicDirty] = useState(Boolean(initialTopic));
  const [goals, setGoals] = useState<string[]>([]);
  const [duration, setDuration] = useState<number>(15);
  const [pagePlan, setPagePlan] = useState<string>("auto");
  const [language, setLanguage] = useState<string>("auto");
  const [gradeValue, setGradeValue] = useState<string>(
    grade === "自动" ? "" : (grade as string));
  const [pedagogy, setPedagogy] = useState("concept_deep@1");
  const [theme, setTheme] = useState("academic_clear@1");
  const [policy, setPolicy] = useState<string>("auto");
  const [research, setResearch] = useState(true);
  const [timeliness, setTimeliness] = useState<string>("basic");
  const [images, setImages] = useState<string>("balanced");
  const [checkpoints, setCheckpoints] = useState<string>("standard");
  const [voiceMode, setVoiceMode] = useState<string>("auto");
  const [voiceId, setVoiceId] = useState<string>("");
  const [voiceFallback, setVoiceFallback] = useState(true);
  const [voiceSpeed, setVoiceSpeed] = useState(0.9);
  const [custom, setCustom] = useState("");
  const [extraSessions, setExtraSessions] = useState<string[]>([]);
  const [moreOpen, setMoreOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warn, setWarn] = useState<string | null>(null);
  const idemRef = useRef("");

  // 数据装载：模板默认值 + 能力 + 本区来源 + 会话。
  useEffect(() => {
    let cancelled = false;
    getClassroomTemplates(lang === "en" ? "en" : "zh")
      .then((tpl) => {
        if (cancelled) return;
        setTemplates(tpl);
        const d = tpl.defaults ?? {};
        if (d.duration_minutes) setDuration(d.duration_minutes);
        if (d.pedagogy_id) setPedagogy(d.pedagogy_id);
        if (d.theme_id) setTheme(d.theme_id);
        if (d.image_density) setImages(d.image_density);
        if (d.checkpoint_density) setCheckpoints(d.checkpoint_density);
      })
      .catch(() => undefined);
    getClassroomCapabilities()
      .then((caps) => {
        if (cancelled) return;
        setCapabilities(caps);
        if (!caps.research?.available) setResearch(false);
        if (caps.tts?.voices?.length) setVoiceId(caps.tts.voices[0].voice_id);
        if (caps.renderer && caps.renderer.available === false) {
          setWarn(tr("cls.form.cap.renderer").replace("%s", caps.renderer.reason ?? ""));
        }
      })
      .catch(() => undefined);
    Promise.all([
      getWorkspace(workspaceId).catch(() => null),
      getTextbooks().catch(() => [] as TextbookListItem[]),
    ]).then(([ws, tbs]) => {
      if (cancelled || !ws) return;
      const textbookByFile = new Map<string, string>();
      for (const tb of tbs ?? []) {
        for (const fid of tb.file_ids ?? []) textbookByFile.set(fid, tb.id);
      }
      const entries: FileEntry[] = (ws.knowledge_files ?? []).map((f) => {
        const tbId = textbookByFile.get(f.library_file_id ?? f.id);
        return {
          fileId: f.library_file_id ?? f.id,
          filename: f.filename,
          isTextbook: Boolean(tbId),
          textbookId: tbId,
          selected: false,
          chapters: [],
          outline: null,
          outlineLoading: false,
        };
      });
      setFiles(entries);
    }).catch(() => undefined);
    listSessions()
      .then((r) => {
        if (cancelled) return;
        setWsSessions((r.sessions ?? [])
          .filter((s) => s.workspace_id === workspaceId)
          .slice(0, 20)
          .map((s) => ({ session_id: s.session_id, title: s.title })));
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, lang]);

  const selectedChapterCount = useMemo(
    () => files.reduce((n, f) => n + f.chapters.length, 0), [files]);

  const toggleFile = useCallback((idx: number) => {
    setFiles((prev) => {
      const next = [...prev];
      const entry = next[idx];
      if (entry.selected) {
        entry.selected = false;
        entry.chapters = [];
      } else {
        // §4.1：最多 8 个文件。
        if (prev.filter((f) => f.selected).length >= 8) return prev;
        entry.selected = true;
      }
      return next;
    });
  }, []);

  const isFileSelected = (f: FileEntry) => f.selected;

  const toggleChapter = useCallback((fileIdx: number, title: string) => {
    let autoTopic = "";
    setFiles((prev) => {
      const next = [...prev];
      const entry = next[fileIdx];
      if (!entry.selected) return prev;
      const has = entry.chapters.some((c) => c.title === title);
      const totalSel = next.reduce((n, f) => n + f.chapters.length, 0);
      if (!has && totalSel >= 12) return prev; // §4.1：最多 12 个章节
      entry.chapters = has
        ? entry.chapters.filter((c) => c.title !== title)
        : [...entry.chapters, { title }];
      if (!has) autoTopic = title;
      return next;
    });
    // 选章节自动填主题（§4.1；用户手改过后不动）。
    if (autoTopic && !topicDirty) setTopic(autoTopic.slice(0, 120));
  }, [topicDirty]);

  const loadOutline = useCallback((idx: number) => {
    setFiles((prev) => {
      const next = [...prev];
      const entry = next[idx];
      if (!entry.textbookId || entry.outline || entry.outlineLoading) return prev;
      entry.outlineLoading = true;
      void getTextbook(entry.textbookId)
        .then((detail) => {
          setFiles((cur) => {
            const copy = [...cur];
            copy[idx] = { ...copy[idx], outline: detail.outline ?? [], outlineLoading: false };
            return copy;
          });
        })
        .catch(() => {
          setFiles((cur) => {
            const copy = [...cur];
            copy[idx] = { ...copy[idx], outline: [], outlineLoading: false };
            return copy;
          });
        });
      return next;
    });
  }, []);

  const effectivePolicy = useMemo(() => {
    if (policy !== "auto") return policy;
    return files.some((f) => isFileSelected(f)) ? "textbook_plus" : "web_topic";
  }, [policy, files]);

  const handleClose = () => {
    if (submitting) return;
    setError(null);
    setWarn(null);
    onClose();
  };

  const topicLen = topic.trim().length;
  const topicError = topicLen === 0 ? "" :
    topicLen < 2 ? tr("cls.form.topic.too.short") :
    topic.length > 120 ? tr("cls.form.topic.too.long") : "";

  const submit = async (startMode: "automatic" | "outline_first") => {
    if (topicLen < 2 || topicLen > 120 || submitting) return;
    setSubmitting(true);
    setError(null);
    idemRef.current =
      `cls_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
    const brief: LessonBrief = {
      topic: topic.trim(),
      goals: goals.map((g) => g.trim()).filter(Boolean).slice(0, 5),
      source_selection: {
        files: files
          .filter((f) => isFileSelected(f))
          .slice(0, 8)
          .map((f) => ({
            file_id: f.fileId,
            chapters: f.chapters.slice(0, 12),
          })),
        extra_sessions: extraSessions.slice(0, 5).map((sid) => ({ session_id: sid })),
      },
      source_policy: effectivePolicy as LessonBrief["source_policy"],
      research: {
        enabled: research && (capabilities?.research?.available ?? false),
        timeliness: timeliness as "basic" | "recent_year" | "recent_month",
      },
      duration_minutes: duration as LessonBrief["duration_minutes"],
      page_plan: pagePlan as LessonBrief["page_plan"],
      language: (language === "auto"
        ? (outputLanguage !== "auto" ? outputLanguage : lang)
        : language) as LessonBrief["language"],
      grade: gradeValue,
      pedagogy_id: pedagogy as LessonBrief["pedagogy_id"],
      theme_id: theme as LessonBrief["theme_id"],
      image_density: images as LessonBrief["image_density"],
      checkpoint_density: checkpoints as LessonBrief["checkpoint_density"],
      voice_preferences: {
        policy: voiceMode as "auto" | "cloud" | "local" | "silent",
        voice_id: voiceId || undefined,
        allow_local_fallback: voiceFallback,
        playback_speed: voiceSpeed,
      },
      custom_requirements: custom.slice(0, 1000),
    };
    try {
      const res = await createLesson(
        workspaceId, { brief, start_mode: startMode }, idemRef.current);
      onCreated(res.lesson_id);
    } catch (e) {
      const msg = e instanceof ClassroomApiError
        ? `${e.code}: ${e.message}` : (e as Error).message;
      setError(msg);
      setSubmitting(false);
    }
  };

  const researchUnavailable = capabilities?.research?.available === false;
  const imagesUnavailable = capabilities?.images?.available === false;
  const ttsUnavailable = capabilities?.tts?.available === false;
  const voices = capabilities?.tts?.voices ?? [];
  const grades = [{ token: "__auto__", label: tr("cls.form.grade.auto") },
    ...GRADE_LABELS[lang]];

  return (
    <Modal
      open={open}
      onClose={handleClose}
      width={880}
      title={
        <div className="flex items-baseline gap-2">
          <span className="text-[1.02rem]">{tr("cls.form.title")}</span>
          {workspaceName && (
            <span className="truncate text-[0.72rem] font-normal text-muted">{workspaceName}</span>
          )}
        </div>
      }
      footer={
        <div className="w-full">
          <p className="mb-2 text-[0.65rem] leading-relaxed text-muted/80">
            {tr("cls.form.privacy")}
          </p>
          {error && <p className="mb-2 text-[0.72rem] text-danger">{tr("cls.form.error")}：{error}</p>}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button variant="ghost" size="sm" disabled={submitting} onClick={handleClose}>
              {tr("cls.form.cancel")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={submitting || topicLen < 2 || topicLen > 120}
              onClick={() => void submit("outline_first")}
            >
              {tr("cls.form.outline.first")}
            </Button>
            <Button
              size="sm"
              disabled={submitting || topicLen < 2 || topicLen > 120}
              onClick={() => void submit("automatic")}
            >
              {submitting
                ? <><Loader2 size={13} className="animate-spin" />{tr("cls.form.submitting")}</>
                : tr("cls.form.submit")}
            </Button>
          </div>
        </div>
      }
    >
      <div className="flex flex-col gap-4 pr-1">
        {warn && (
          <p className="rounded-[8px] border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[0.72rem] text-amber-700 dark:text-amber-400">
            {warn}
          </p>
        )}

        {/* 主题 */}
        <Field label={`${tr("cls.form.topic")}（${tr("cls.form.topic.count").replace("%n", String(topic.length))}）`}>
          <Input
            value={topic}
            maxLength={140}
            placeholder={tr("cls.form.topic.placeholder")}
            onChange={(e) => { setTopicDirty(true); setTopic(e.target.value); }}
            aria-invalid={Boolean(topicError)}
          />
          {topicError && <p className="mt-1 text-[0.7rem] text-danger">{topicError}</p>}
        </Field>

        {/* 来源：教材/章节 + 额外对话材料 */}
        <Field label={tr("cls.form.sources")}>
          <p className="mb-2 text-[0.68rem] leading-relaxed text-muted">
            {tr("cls.form.sources.desc")}
          </p>
          {files.length === 0 ? (
            <div className="rounded-[8px] border border-dashed border-border px-3 py-3 text-[0.72rem] text-muted">
              <p>{tr("cls.form.sources.empty")}</p>
              <p className="mt-1 text-accent-strong/90">{tr("cls.form.sources.general.hint")}</p>
            </div>
          ) : (
            <div className="flex max-h-52 flex-col gap-1 overflow-y-auto rounded-[8px] border border-border p-1.5">
              {files.map((f, idx) => {
                const selected = isFileSelected(f);
                return (
                  <div key={f.fileId} className="rounded-[6px]">
                    <div className="flex items-center gap-2 px-1 py-1">
                      <input
                        id={`cls-file-${idx}`}
                        type="checkbox"
                        checked={selected}
                        onChange={() => toggleFile(idx)}
                        className="size-4 shrink-0 cursor-pointer accent-accent"
                      />
                      <label
                        htmlFor={`cls-file-${idx}`}
                        className="flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 text-[0.75rem] text-fg-secondary"
                      >
                        {f.isTextbook ? <BookOpen size={12} className="text-accent" /> : <FileText size={12} className="text-muted" />}
                        <span className="truncate">{f.filename}</span>
                      </label>
                      {selected && f.isTextbook && (
                        <button
                          type="button"
                          onClick={() => loadOutline(idx)}
                          className="shrink-0 cursor-pointer text-[0.68rem] text-accent-strong hover:underline"
                        >
                          {f.outlineLoading ? tr("cls.form.sources.chapters.loading") : tr("cls.form.sources.chapters.load")}
                        </button>
                      )}
                      {selected && !f.isTextbook && (
                        <span className="shrink-0 text-[0.65rem] text-muted">{tr("cls.form.sources.chapters.none")}</span>
                      )}
                    </div>
                    {selected && f.outline && f.outline.length > 0 && (
                      <div className="ml-7 mt-0.5 flex flex-wrap gap-1 pb-1.5">
                        {f.outline.map((ch) => {
                          const on = f.chapters.some((c) => c.title === ch.chapter);
                          return (
                            <button
                              key={ch.chapter}
                              type="button"
                              onClick={() => toggleChapter(idx, ch.chapter)}
                              className={cn(
                                "cursor-pointer rounded-full border px-2 py-0.5 text-[0.65rem] transition-colors",
                                on
                                  ? "border-accent bg-accent-soft/50 text-accent-strong"
                                  : "border-border text-muted hover:border-accent/50 hover:text-fg-secondary",
                              )}
                            >
                              {ch.chapter}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {selectedChapterCount > 0 && (
            <p className="mt-1 text-[0.65rem] text-muted">
              {tr("cls.form.sources.chapters.count").replace("%n", String(selectedChapterCount))}
            </p>
          )}
        </Field>

        {/* 额外对话材料（默认不选） */}
        {wsSessions.length > 0 && (
          <Field label={tr("cls.form.sources.sessions")}>
            <p className="mb-1.5 text-[0.68rem] leading-relaxed text-muted">
              {tr("cls.form.sources.sessions.desc")}
            </p>
            <div className="flex max-h-28 flex-col gap-0.5 overflow-y-auto rounded-[8px] border border-border p-1.5">
              {wsSessions.map((s) => (
                <label key={s.session_id} className="flex cursor-pointer items-center gap-2 px-1 py-0.5 text-[0.72rem] text-fg-secondary">
                  <input
                    type="checkbox"
                    checked={extraSessions.includes(s.session_id)}
                    onChange={() => setExtraSessions((prev) =>
                      prev.includes(s.session_id)
                        ? prev.filter((x) => x !== s.session_id)
                        : [...prev, s.session_id])}
                    className="size-3.5 shrink-0 cursor-pointer accent-accent"
                  />
                  <MessageSquareText size={11} className="shrink-0 text-muted" />
                  <span className="truncate">{s.title}</span>
                </label>
              ))}
            </div>
          </Field>
        )}

        {/* 时长 */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label={tr("cls.form.duration")}>
            <Select
              value={String(duration)}
              onChange={(v) => setDuration(Number(v))}
              options={DURATIONS.map((d) => ({
                value: String(d),
                label: `${d} ${tr("cls.form.duration.unit")}`,
              }))}
            />
            <p className="mt-1 text-[0.65rem] text-muted/80">{tr("cls.form.duration.hint")}</p>
          </Field>
          <Field label={tr("cls.form.pages")}>
            <Select
              value={pagePlan}
              onChange={setPagePlan}
              options={[
                { value: "auto", label: tr("cls.form.pages.auto") },
                ...PAGE_PLANS.map((p) => ({
                  value: p.value,
                  label: tr("cls.form.pages.range").replace("%n", String(p.n)),
                })),
              ]}
            />
            <p className="mt-1 text-[0.65rem] text-muted/80">{tr("cls.form.pages.hint")}</p>
          </Field>
        </div>

        {/* 教学模板：5 选 1，一句说明 */}
        <Field label={tr("cls.form.pedagogy")}>
          <div role="radiogroup" aria-label={tr("cls.form.pedagogy")} className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
            {(templates?.pedagogy ?? []).map((p) => {
              const name = lang === "en" ? p.name_en : p.name_zh;
              const desc = lang === "en" ? p.description_en : p.description_zh;
              const on = pedagogy === p.pedagogy_id;
              return (
                <button
                  key={p.pedagogy_id}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  title={desc}
                  onClick={() => setPedagogy(p.pedagogy_id)}
                  className={cn(
                    "cursor-pointer rounded-[8px] border px-2.5 py-2 text-left transition-all",
                    on ? "border-accent bg-accent-soft/30" : "border-border hover:border-accent/50",
                  )}
                >
                  <p className={cn("text-[0.75rem] font-medium", on ? "text-accent-strong" : "text-fg")}>{name}</p>
                  <p className="mt-0.5 line-clamp-2 text-[0.63rem] leading-snug text-muted">{desc}</p>
                </button>
              );
            })}
          </div>
        </Field>

        {/* 视觉模板 */}
        <Field label={tr("cls.form.theme")}>
          <TemplatePicker
            themes={templates?.themes ?? []}
            value={theme}
            onChange={setTheme}
          />
        </Field>

        {/* 更多设置 */}
        <div>
          <button
            type="button"
            onClick={() => setMoreOpen((v) => !v)}
            aria-expanded={moreOpen}
            className="flex w-full cursor-pointer items-center gap-1 rounded-[8px] border border-border px-3 py-2 text-[0.75rem] font-medium text-fg-secondary transition-colors hover:border-accent/40 hover:text-fg"
          >
            {moreOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {tr("cls.form.more")}
          </button>
          {moreOpen && (
            <div className="mt-3 flex flex-col gap-4 rounded-[10px] border border-border-light bg-surface-sunken/40 p-3.5">
              {/* 学习目标 */}
              <Field label={tr("cls.form.goals")}>
                {goals.map((g, i) => (
                  <div key={i} className="mb-1.5 flex items-center gap-1.5">
                    <Input
                      value={g}
                      maxLength={120}
                      placeholder={tr("cls.form.goals.placeholder")}
                      onChange={(e) => setGoals((prev) =>
                        prev.map((x, j) => j === i ? e.target.value : x))}
                    />
                    <button
                      type="button"
                      onClick={() => setGoals((prev) => prev.filter((_, j) => j !== i))}
                      className="shrink-0 cursor-pointer p-1 text-muted hover:text-danger"
                      aria-label="remove goal"
                    >
                      <X size={13} />
                    </button>
                  </div>
                ))}
                {goals.length < 5 && (
                  <Button variant="ghost" size="sm" icon={<Plus size={12} />} onClick={() => setGoals((p) => [...p, ""])}>
                    {tr("cls.form.goals.add")}
                  </Button>
                )}
              </Field>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label={tr("cls.form.grade")}>
                  <Select
                    value={gradeValue === "" ? "__auto__" : gradeValue}
                    onChange={(v) => setGradeValue(v === "__auto__" ? "" : v)}
                    options={grades.map((g) => ({ value: g.token, label: g.label }))}
                  />
                </Field>
                <Field label={tr("cls.form.language")}>
                  <Select
                    value={language}
                    onChange={setLanguage}
                    options={[
                      { value: "auto", label: tr("cls.form.language.auto") },
                      { value: "zh", label: "中文" },
                      { value: "en", label: "English" },
                    ]}
                  />
                </Field>
              </div>

              <Field label={tr("cls.form.policy")}>
                <Select
                  value={policy}
                  onChange={setPolicy}
                  options={[
                    { value: "auto", label: tr("cls.form.policy.auto") },
                    { value: "strict_textbook", label: tr("cls.form.policy.strict_textbook") },
                    { value: "textbook_plus", label: tr("cls.form.policy.textbook_plus") },
                    { value: "web_topic", label: tr("cls.form.policy.web_topic") },
                  ]}
                />
              </Field>

              <Field label={tr("cls.form.research")}>
                <label className="flex cursor-pointer items-center gap-2 text-[0.75rem] text-fg-secondary">
                  <input
                    type="checkbox"
                    checked={research && !researchUnavailable}
                    disabled={researchUnavailable}
                    onChange={(e) => setResearch(e.target.checked)}
                    className="size-4 cursor-pointer accent-accent disabled:cursor-not-allowed"
                  />
                  {tr("cls.form.research.desc")}
                </label>
                {researchUnavailable && (
                  <p className="mt-1 text-[0.68rem] text-muted">
                    {tr("cls.form.research.unavailable").replace("%s", capabilities?.research?.reason ?? "")}
                  </p>
                )}
                {!researchUnavailable && (
                  <div className="mt-2">
                    <Select
                      value={timeliness}
                      onChange={setTimeliness}
                      options={TIMELINESS.map((v) => ({
                        value: v, label: tr(`cls.form.timeliness.${v}`),
                      }))}
                    />
                  </div>
                )}
              </Field>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field label={tr("cls.form.image")}>
                  <Select
                    value={images}
                    onChange={setImages}
                    options={["none", "balanced", "rich"].map((v) => ({
                      value: v, label: tr(`cls.form.image.${v}`),
                    }))}
                  />
                  {imagesUnavailable && images !== "none" && (
                    <p className="mt-1 text-[0.68rem] text-muted">
                      {tr("cls.form.image.unavailable").replace("%s", capabilities?.images?.reason ?? "")}
                    </p>
                  )}
                  <p className="mt-1 text-[0.65rem] text-muted/80">{tr("cls.form.image.hint")}</p>
                </Field>
                <Field label={tr("cls.form.checkpoint")}>
                  <Select
                    value={checkpoints}
                    onChange={setCheckpoints}
                    options={["none", "light", "standard"].map((v) => ({
                      value: v, label: tr(`cls.form.checkpoint.${v}`),
                    }))}
                  />
                  <p className="mt-1 text-[0.65rem] text-muted/80">{tr("cls.form.checkpoint.hint")}</p>
                </Field>
              </div>

              <Field label={tr("cls.form.voice")}>
                {ttsUnavailable && (
                  <p className="mb-1.5 text-[0.68rem] text-muted">
                    {tr("cls.form.voice.unavailable").replace("%s", capabilities?.tts?.reason ?? "")}
                  </p>
                )}
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Select
                    value={voiceMode}
                    onChange={setVoiceMode}
                    options={VOICE_MODES.filter((m) =>
                      m !== "cloud" || !ttsUnavailable).map((m) => ({
                      value: m,
                      label: m === "auto" ? tr("cls.form.voice.auto") : tr(`cls.form.voice.${m}`),
                    }))}
                  />
                  {voices.length > 0 && (
                    <Select
                      value={voiceId}
                      onChange={setVoiceId}
                      options={voices.map((v) => ({ value: v.voice_id, label: v.display_name }))}
                    />
                  )}
                </div>
                <label className="mt-2 flex cursor-pointer items-center gap-2 text-[0.72rem] text-fg-secondary">
                  <input
                    type="checkbox"
                    checked={voiceFallback}
                    onChange={(e) => setVoiceFallback(e.target.checked)}
                    className="size-3.5 cursor-pointer accent-accent"
                  />
                  {tr("cls.form.voice.fallback")}
                  <span className="text-muted/70">{tr("cls.form.voice.fallback.hint")}</span>
                </label>
                <div className="mt-2 flex items-center gap-2">
                  <input
                    type="range"
                    min={0.5}
                    max={1.5}
                    step={0.05}
                    value={voiceSpeed}
                    onChange={(e) => setVoiceSpeed(Number(e.target.value))}
                    className="w-40 cursor-pointer accent-accent"
                    aria-label={tr("cls.form.voice.speed")}
                  />
                  <span className="tnum text-[0.7rem] text-muted">
                    {tr("cls.form.voice.speed").replace("%s", voiceSpeed.toFixed(2))}
                  </span>
                </div>
              </Field>

              <Field label={`${tr("cls.form.custom")}（${tr("cls.form.custom.count").replace("%n", String(custom.length))}）`}>
                <Textarea
                  rows={3}
                  maxLength={1000}
                  value={custom}
                  placeholder={tr("cls.form.custom.placeholder")}
                  onChange={(e) => setCustom(e.target.value)}
                />
              </Field>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
