"use client";
// AI 生成向导：三步（模板 → 来源 → 确认）。来源三形态：
// 从对话（教材/文件按对话引用自动推导，无需另选工作区/教材）、
// 从工作区（可限定某些对话或整个工作区，教材 = 工作区教材 + 所选对话
// 额外引用）、从教材（直接对教材写笔记）；错题本为附加项。
// 生成过程 SSE 流式预览。
import { useEffect, useMemo, useState } from "react";
import {
  BookOpen, Check, ChevronLeft, ChevronRight, FolderTree, MessageSquare,
  Sparkles, X, Zap,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { cn } from "@/lib/cn";
import { notesGenerateStream } from "@/lib/api-notes";
import { listSessions, getTextbooks, listWorkspaces } from "@/lib/api";
import type { SessionItem } from "@/lib/types";
import type { TextbookListItem } from "@/lib/api";
import type { NoteTemplate, NoteSummary } from "@/lib/types-notes";
import { relTime } from "@/lib/format";

const SOURCE_MODES = [
  { key: "sessions", icon: MessageSquare,
    labelKey: "gw.sourceMode.sessions", descKey: "gw.sourceMode.sessions.desc" },
  { key: "workspace", icon: FolderTree,
    labelKey: "gw.sourceMode.workspace", descKey: "gw.sourceMode.workspace.desc" },
  { key: "textbooks", icon: BookOpen,
    labelKey: "gw.sourceMode.textbooks", descKey: "gw.sourceMode.textbooks.desc" },
] as const;

type SourceMode = (typeof SOURCE_MODES)[number]["key"];

interface GenerateWizardProps {
  open: boolean;
  onClose: () => void;
  templates: NoteTemplate[];
  tr: (k: string, fallback?: string) => string;
  lang: "zh" | "en";
  onCreated: (note: NoteSummary) => void;
  onVaultChanged: () => void;
}

export function GenerateWizard({
  open, onClose, templates, tr, lang, onCreated, onVaultChanged,
}: GenerateWizardProps) {
  const [step, setStep] = useState(0);
  const [templateId, setTemplateId] = useState("");
  const [sourceMode, setSourceMode] = useState<SourceMode>("sessions");
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [textbooks, setTextbooks] = useState<TextbookListItem[]>([]);
  const [workspaces, setWorkspaces] = useState<{ workspace_id: string; name: string }[]>([]);
  const [pickedSessions, setPickedSessions] = useState<Set<string>>(new Set());
  const [pickedWsSessions, setPickedWsSessions] = useState<Set<string>>(new Set());
  const [pickedTextbooks, setPickedTextbooks] = useState<Set<string>>(new Set());
  const [workspaceId, setWorkspaceId] = useState("");
  const [useErrorNotebook, setUseErrorNotebook] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [generating, setGenerating] = useState(false);
  const [draft, setDraft] = useState("");
  const [summary, setSummary] = useState("");
  const [error, setError] = useState("");
  const [created, setCreated] = useState<NoteSummary | null>(null);

  // 打开即挂载（NotesView 条件渲染），初始 state 即默认值；这里只拉来源清单。
  useEffect(() => {
    if (!open) return;
    let alive = true;
    listSessions()
      .then((r) => { if (alive) setSessions(r.sessions.slice(0, 50)); })
      .catch(() => { if (alive) setSessions([]); });
    getTextbooks()
      .then((t) => { if (alive) setTextbooks(t); })
      .catch(() => { if (alive) setTextbooks([]); });
    listWorkspaces()
      .then((r) => { if (alive) setWorkspaces(r.workspaces); })
      .catch(() => { if (alive) setWorkspaces([]); });
    return () => { alive = false; };
  }, [open]);

  const builtin = useMemo(() => templates.filter((t) => t.builtin), [templates]);
  const wsName = useMemo(() => Object.fromEntries(
    workspaces.map((w) => [w.workspace_id, w.name])), [workspaces]);
  const wsSessions = useMemo(
    () => sessions.filter((s) => s.workspace_id === workspaceId),
    [sessions, workspaceId]);
  const modeValid = sourceMode === "workspace"
    ? !!workspaceId
    : sourceMode === "textbooks"
      ? pickedTextbooks.size > 0
      : pickedSessions.size > 0;
  const hasSource = modeValid || useErrorNotebook;

  const sessionSub = (s: SessionItem) => {
    const ws = s.workspace_id ? wsName[s.workspace_id] : "";
    return ws ? `${relTime(s.updated_at, lang)} · ${ws}` : relTime(s.updated_at, lang);
  };

  const toggle = (set: Set<string>, id: string, setter: (s: Set<string>) => void) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  };

  const generate = async () => {
    setGenerating(true);
    setError("");
    setDraft("");
    try {
      for await (const ev of notesGenerateStream({
        template_id: templateId,
        sources: {
          source_mode: sourceMode,
          session_ids: sourceMode === "workspace" ? [...pickedWsSessions] : [...pickedSessions],
          textbook_ids: sourceMode === "textbooks" ? [...pickedTextbooks] : [],
          workspace_id: sourceMode === "workspace" ? workspaceId : undefined,
          use_error_notebook: useErrorNotebook,
        },
        instructions,
      })) {
        switch (ev.type) {
          case "sources_summary":
            setSummary(tr("gw.collected", "已收集 {sessions} 个对话 · {textbooks} 本教材 · 检索到 {retrieved} 个相关片段")
              .replace("{sessions}", String(ev.sessions ?? 0))
              .replace("{textbooks}", String(ev.textbooks ?? 0))
              .replace("{retrieved}", String(ev.retrieved ?? 0)));
            break;
          case "answer":
            if (ev.is_delta) setDraft((prev) => prev + String(ev.content ?? ""));
            break;
          case "note_created":
            setCreated(ev.note as NoteSummary);
            onVaultChanged();
            break;
          case "error":
            setError(String(ev.message ?? tr("gw.error")));
            break;
          default:
            break;
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : tr("gw.error"));
    } finally {
      setGenerating(false);
    }
  };

  const steps = [tr("gw.step.template"), tr("gw.step.sources"), tr("gw.step.confirm")];

  return (
    <Modal open={open} onClose={generating ? () => {} : onClose} width={640}>
      <div className="mb-4 flex items-center gap-2">
        <Sparkles size={16} className="text-accent2" />
        <span className="text-sm font-semibold text-fg">{tr("gw.title")}</span>
        {!generating && !created && (
          <button onClick={onClose} className="ml-auto cursor-pointer text-muted hover:text-fg">
            <X size={15} />
          </button>
        )}
      </div>

      {/* 步骤指示 */}
      {!generating && !created && (
        <div className="mb-4 flex items-center gap-1.5 text-[11px]">
          {steps.map((s, i) => (
            <span key={s} className={cn(
              "flex items-center gap-1",
              i === step ? "font-semibold text-accent" : i < step ? "text-success" : "text-muted",
            )}>
              {i > 0 && <ChevronLeft size={10} className="rotate-180" />}
              {i < step ? <Check size={11} /> : <span className="tnum">{i + 1}.</span>} {s}
            </span>
          ))}
        </div>
      )}

      {/* Step 0: 模板 */}
      {!generating && !created && step === 0 && (
        <div className="grid grid-cols-1 gap-2">
          {builtin.map((t) => (
            <button
              key={t.id}
              onClick={() => setTemplateId(t.id)}
              className={cn(
                "flex cursor-pointer items-start gap-2.5 rounded-[10px] border px-3 py-2.5 text-left transition-colors",
                templateId === t.id
                  ? "border-accent bg-accent-soft"
                  : "border-border hover:border-accent/50",
              )}
            >
              <Zap size={14} className={cn("mt-0.5", templateId === t.id ? "text-accent" : "text-muted")} />
              <div className="min-w-0">
                <div className="text-xs font-medium text-fg">
                  {lang === "en" && t.name_en ? t.name_en : t.name}
                  {t.review_enabled && (
                    <span className="ml-1.5 rounded bg-accent2/10 px-1 py-0.5 text-[10px] text-accent2">
                      SM-2
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[11px] leading-relaxed text-muted">{t.description}</div>
              </div>
            </button>
          ))}
          <div className="mt-2 flex justify-end">
            <Button size="sm" disabled={!templateId} onClick={() => setStep(1)}
              icon={<ChevronRight size={13} />}>
              {tr("gw.next")}
            </Button>
          </div>
        </div>
      )}

      {/* Step 1: 来源（三形态） */}
      {!generating && !created && step === 1 && (
        <div className="max-h-[52vh] space-y-4 overflow-y-auto pr-1">
          <div>
            <div className="mb-1.5 text-xs font-medium text-fg">{tr("gw.sourceMode")}</div>
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3">
              {SOURCE_MODES.map((m) => (
                <button
                  key={m.key}
                  onClick={() => setSourceMode(m.key)}
                  className={cn(
                    "flex cursor-pointer items-start gap-2 rounded-[10px] border px-2.5 py-2 text-left transition-colors",
                    sourceMode === m.key
                      ? "border-accent bg-accent-soft"
                      : "border-border hover:border-accent/50",
                  )}
                >
                  <m.icon size={14} className={cn(
                    "mt-0.5 shrink-0",
                    sourceMode === m.key ? "text-accent" : "text-muted",
                  )} />
                  <span className="min-w-0">
                    <span className={cn(
                      "block text-xs font-medium",
                      sourceMode === m.key ? "text-accent-strong" : "text-fg",
                    )}>
                      {tr(m.labelKey)}
                    </span>
                    <span className="mt-0.5 block text-[10px] leading-snug text-muted">
                      {tr(m.descKey)}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </div>

          {sourceMode === "sessions" && (
            <SourceSection icon={<MessageSquare size={13} />} title={tr("gw.sessions")}
              desc={tr("gw.sessions.desc")}>
              {sessions.length === 0 && <div className="text-[11px] text-muted">—</div>}
              {sessions.map((s) => (
                <PickRow key={s.session_id} picked={pickedSessions.has(s.session_id)}
                  onClick={() => toggle(pickedSessions, s.session_id, setPickedSessions)}
                  title={s.title || s.session_id}
                  sub={sessionSub(s)} />
              ))}
            </SourceSection>
          )}

          {sourceMode === "workspace" && (
            <>
              <div>
                <div className="mb-1.5 text-xs font-medium text-fg">{tr("gw.workspace.pick")}</div>
                <select
                  value={workspaceId}
                  onChange={(e) => {
                    setWorkspaceId(e.target.value);
                    setPickedWsSessions(new Set());
                  }}
                  className="h-7 w-full cursor-pointer rounded-md border border-border bg-bg px-1.5 text-xs text-fg-secondary outline-none focus:border-accent"
                >
                  <option value="">—</option>
                  {workspaces.map((w) => (
                    <option key={w.workspace_id} value={w.workspace_id}>{w.name}</option>
                  ))}
                </select>
              </div>
              {workspaceId && (
                <SourceSection icon={<MessageSquare size={13} />} title={tr("gw.workspace.sessions")}
                  desc={tr("gw.workspace.sessions.desc")}>
                  {wsSessions.length === 0 && <div className="text-[11px] text-muted">—</div>}
                  {wsSessions.map((s) => (
                    <PickRow key={s.session_id} picked={pickedWsSessions.has(s.session_id)}
                      onClick={() => toggle(pickedWsSessions, s.session_id, setPickedWsSessions)}
                      title={s.title || s.session_id}
                      sub={relTime(s.updated_at, lang)} />
                  ))}
                </SourceSection>
              )}
            </>
          )}

          {sourceMode === "textbooks" && (
            <SourceSection icon={<BookOpen size={13} />} title={tr("gw.textbooks")}
              desc={tr("gw.textbooks.desc")}>
              {textbooks.length === 0 && <div className="text-[11px] text-muted">—</div>}
              {textbooks.map((t) => (
                <PickRow key={t.id} picked={pickedTextbooks.has(t.id)}
                  onClick={() => toggle(pickedTextbooks, t.id, setPickedTextbooks)}
                  title={t.title || t.group_name || t.id}
                  sub={`${t.subject || ""} · ${chapterLabel(t)}`} />
              ))}
            </SourceSection>
          )}

          <label className="flex cursor-pointer items-center gap-2 rounded-[10px] border border-border px-3 py-2.5">
            <input type="checkbox" checked={useErrorNotebook}
              onChange={(e) => setUseErrorNotebook(e.target.checked)}
              className="accent-(--color-accent) size-3.5" />
            <div>
              <div className="text-xs font-medium text-fg">{tr("gw.errorNotebook")}</div>
              <div className="text-[11px] text-muted">{tr("gw.errorNotebook.desc")}</div>
            </div>
          </label>
          <div className="flex justify-between">
            <Button variant="ghost" size="sm" onClick={() => setStep(0)}
              icon={<ChevronLeft size={13} />}>
              {tr("gw.back")}
            </Button>
            <Button size="sm" disabled={!hasSource} onClick={() => setStep(2)}
              icon={<ChevronRight size={13} />}>
              {tr("gw.next")}
            </Button>
          </div>
        </div>
      )}

      {/* Step 2: 确认 */}
      {!generating && !created && step === 2 && (
        <div className="space-y-3">
          <div className="rounded-[10px] border border-border bg-bg p-3 text-[11px] text-fg-secondary">
            <div>{summary || (hasSource ? "" : tr("gw.selectAtLeastOne"))}</div>
            <div className="mt-1 text-muted">
              {sourceMode === "sessions" && `${tr("gw.sessions")}: ${pickedSessions.size}`}
              {sourceMode === "workspace" && (
                `${tr("gw.workspace")}: ${wsName[workspaceId] || workspaceId}`
                + (pickedWsSessions.size > 0
                  ? ` · ${tr("gw.sessions")} ${pickedWsSessions.size}` : "")
              )}
              {sourceMode === "textbooks" && `${tr("gw.textbooks")}: ${pickedTextbooks.size}`}
              {useErrorNotebook && ` · ${tr("gw.errorNotebook")}`}
            </div>
            <div className="mt-1 text-muted">{tr("gw.retrieve.hint")}</div>
          </div>
          <div>
            <div className="mb-1 text-xs font-medium text-fg">{tr("gw.instructions")}</div>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder={tr("gw.instructions.placeholder")}
              rows={3}
              className="w-full resize-none rounded-[8px] border border-border bg-bg px-2.5 py-1.5 text-xs text-fg outline-none focus:border-accent"
            />
          </div>
          <div className="flex justify-between">
            <Button variant="ghost" size="sm" onClick={() => setStep(1)}
              icon={<ChevronLeft size={13} />}>
              {tr("gw.back")}
            </Button>
            <Button variant="accent2" size="sm" icon={<Sparkles size={13} />} onClick={() => void generate()}>
              {tr("gw.generate")}
            </Button>
          </div>
        </div>
      )}

      {/* 生成中 / 完成 */}
      {(generating || created) && (
        <div className="space-y-3">
          {generating && (
            <div className="flex items-center gap-2 text-xs text-fg-secondary">
              <span className="dot-loader" /> {tr("gw.generating")} {summary}
            </div>
          )}
          {created && (
            <div className="flex items-center gap-2 rounded-md bg-success/10 px-2.5 py-1.5 text-xs text-success">
              <Check size={13} /> {tr("gw.done")}：《{created.title}》
            </div>
          )}
          <div className="max-h-[46vh] overflow-y-auto rounded-[10px] border border-border bg-bg p-3">
            <pre className="whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-fg-secondary">
              {draft || "…"}
            </pre>
          </div>
          {error && <div className="text-xs text-danger">{error}</div>}
          <div className="flex justify-end gap-2">
            {!generating && (
              <>
                <Button variant="outline" size="sm" onClick={onClose}>{tr("gw.back")}</Button>
                {created && (
                  <Button variant="primary" size="sm" onClick={() => {
                    onCreated(created);
                    onClose();
                  }}>
                    {tr("gw.openNote")}
                  </Button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function chapterLabel(t: TextbookListItem): string {
  const chapters = t.chapter_count ?? 0;
  const volumes = (t.volumes ?? []).length;
  const vols = volumes > 0 ? ` · ${volumes}卷` : "";
  return `${chapters}章${vols} · ${t.scope === "public" ? "公用" : "私有"}`;
}

function SourceSection({
  icon, title, desc, children,
}: {
  icon: React.ReactNode;
  title: string;
  desc?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-fg">
        {icon} {title}
      </div>
      {desc && <div className="mb-1.5 text-[11px] text-muted">{desc}</div>}
      <div className="max-h-40 space-y-1 overflow-y-auto rounded-[10px] border border-border p-1.5">
        {children}
      </div>
    </div>
  );
}

function PickRow({
  picked, onClick, title, sub,
}: {
  picked: boolean;
  onClick: () => void;
  title: string;
  sub: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex w-full cursor-pointer items-center gap-2 rounded-md border px-2 py-1.5 text-left transition-colors",
        picked ? "border-accent/40 bg-accent-soft" : "border-transparent hover:bg-surface-hover",
      )}
    >
      <span className={cn(
        "flex size-3.5 shrink-0 items-center justify-center rounded border",
        picked ? "border-accent bg-accent text-white" : "border-border",
      )}>
        {picked && <Check size={10} />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs text-fg">{title}</span>
        <span className="block truncate text-[10px] text-muted">{sub}</span>
      </span>
    </button>
  );
}
