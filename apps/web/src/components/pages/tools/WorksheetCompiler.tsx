"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { ApiError } from "@next-tutor/api-client";
import { Button } from "@/components/ui/Button";
import { Field, FIELD_CLS, Input, Textarea } from "@/components/ui/Input";
import { Modal, ConfirmModal } from "@/components/ui/Modal";
import { Pager, paged } from "@/components/ui/Pager";
import { Markdown } from "@/components/chat/markdown";
import { useUIStore } from "@/lib/store";
import { useToast } from "@/components/ui/Toast";
import { apiClient } from "@/platform/api-client";
import { getEvalConcepts } from "@/lib/api-modules";
import { WS_CHANGED_EVENT, useWsSettings } from "@/lib/ws-settings";
import type { WorkspaceItem } from "@/lib/types";
import type { ConceptEvaluationView } from "@/lib/types-modules";
import type { ImageGenerationResult } from "@/lib/api-image-generation";
import { ImageGenerationStudio } from "./ImageGenerationStudio";
import { BackMark, ComposeMark, ExportMark, PaperCompilerMark, RevisionMark } from "./ToolMarks";
import { PaperPreview, SingleQuestionPreview } from "./WorksheetPreview";
import { printWorksheetHtml } from "./worksheet-print";
import { worksheetText, worksheetExtra, type WorksheetCopy } from "./worksheet-copy";
import {
  attachWorksheetImage, createWorksheet, deleteWorksheet, deleteWorksheetQuestion, exportWorksheet, generateWorksheet, getWorksheet,
  listWorksheets, patchWorksheet, patchWorksheetQuestion, refineWorksheetQuestion,
  type WorksheetDocument, type WorksheetQuestion, type WorksheetQuestionPatchRequest, type WorksheetCreateRequest,
} from "@/lib/api-worksheets";

type Draft = { id: string; title: string; question_count: number; total_score: number; updated_at: number; status: "draft" | "ready" };
type Message = { role: "assistant" | "user"; text: string };
type GenerationValues = { type: WorksheetQuestion["type"]; difficulty: string; score: string; count: string; points: string; hint: string };
const generationDefaults: GenerationValues = { type: "multiple_choice", difficulty: "3", score: "5", count: "5", points: "", hint: "" };

export function WorksheetCompiler({ initialId = "" }: { initialId?: string }) {
  const lang = useUIStore((state) => state.lang);
  const tr = { ...worksheetText[lang as "zh" | "en"], ...worksheetExtra[lang as "zh" | "en"] } as WorksheetCopy;
  const toast = useToast();
  const alive = useRef(true);
  const [paper, setPaper] = useState<WorksheetDocument | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [areas, setAreas] = useState<WorkspaceItem[]>([]);
  const [areasReady, setAreasReady] = useState(false);
  const [areaError, setAreaError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [activeId, setActiveId] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [generationMode, setGenerationMode] = useState<"single" | "batch" | null>(null);
  const [generation, setGeneration] = useState<GenerationValues>(generationDefaults);
  const [editorOpen, setEditorOpen] = useState(false);
  const [imageOpen, setImageOpen] = useState(false);
  const [paperOpen, setPaperOpen] = useState(false);
  const [showAnswers, setShowAnswers] = useState(false);
  const [paperAnswers, setPaperAnswers] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<"paper" | "question" | null>(null);
  const [messages, setMessages] = useState<Record<string, Message[]>>({});
  const [instruction, setInstruction] = useState("");
  const [composeMode, setComposeMode] = useState<"new" | "revise">("new");

  const updatePaper = useCallback((value: WorksheetDocument) => {
    if (!alive.current) return;
    setPaper(value);
    const row: Draft = { id: value.id, title: value.title, question_count: value.questions?.length ?? 0, total_score: value.total_score ?? 0, updated_at: value.updated_at, status: value.status ?? "draft" };
    setDrafts((previous) => [row, ...previous.filter((draft) => draft.id !== row.id)]);
  }, []);

  const reloadAreas = useCallback(async () => {
    try {
      const value = await apiClient().workspace.list<{ workspaces: WorkspaceItem[] }>();
      if (alive.current) { setAreas(value.workspaces ?? []); setAreaError(false); }
    } catch {
      if (alive.current) setAreaError(true);
    } finally { if (alive.current) setAreasReady(true); }
  }, []);

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    const areaTimer = window.setTimeout(() => { void reloadAreas(); }, 0);
    window.addEventListener(WS_CHANGED_EVENT, reloadAreas);
    Promise.all([listWorksheets(controller.signal), initialId ? getWorksheet(initialId, controller.signal) : Promise.resolve(null)])
      .then(([list, value]) => {
        if (!alive.current) return;
        setDrafts(list.items);
        if (value) {
          setPaper(value); setActiveId(value.questions?.[0]?.id ?? "");
          setComposeMode(value.questions?.length ? "revise" : "new");
          if (!value.workspace_id) setSettingsOpen(true);
        }
      }).catch(() => { if (alive.current && !controller.signal.aborted) setLoadError(true); })
      .finally(() => { if (alive.current) setLoading(false); });
    return () => {
      alive.current = false; controller.abort();
      window.clearTimeout(areaTimer);
      window.removeEventListener(WS_CHANGED_EVENT, reloadAreas);
    };
  }, [initialId, reloadAreas]);

  const questions = paper?.questions ?? [];
  const active = questions.find((question) => question.id === activeId) ?? null;
  const activeArea = areas.find((area) => area.workspace_id === paper?.workspace_id);
  const validScope = areasReady && Boolean(activeArea);
  const disabled = busy || exportBusy;
  const chat = active ? messages[active.id] ?? [] : messages.new ?? [];

  function report(error: unknown) {
    const code = error instanceof ApiError ? error.code : error instanceof Error ? error.message : "";
    const labels: Record<string, string> = {
      worksheet_revision_conflict: tr.conflict, worksheet_learning_area_required: tr.learningAreaRequired,
      worksheet_learning_area_invalid: tr.areaMissing, worksheet_textbook_scope_empty: tr.scopeEmpty,
      worksheet_textbook_no_match: tr.noEvidence, worksheet_textbook_unavailable: tr.retrievalFailed,
      worksheet_generation_failed: tr.generationFailed, worksheet_generation_unavailable: tr.generationFailed,
      worksheet_refine_failed: tr.refineFailed, worksheet_math_invalid: tr.mathInvalid,
      worksheet_math_renderer_unavailable: tr.printFailed, worksheet_print_timeout: tr.printFailed,
    };
    if (alive.current) toast(labels[code] || tr.requestFailed, "error");
  }

  async function openDraft(id: string) {
    setLoading(true); setLoadError(false);
    try {
      const value = await getWorksheet(id);
      updatePaper(value);
      if (!alive.current) return;
      setActiveId(value.questions?.[0]?.id ?? ""); setMessages({}); setInstruction("");
      setComposeMode(value.questions?.length ? "revise" : "new");
      setSettingsOpen(!value.workspace_id || !areas.some((area) => area.workspace_id === value.workspace_id));
    } catch (error) { report(error); }
    finally { if (alive.current) setLoading(false); }
  }

  async function saveSettings(fields: WorksheetCreateRequest) {
    setBusy(true);
    try {
      const value = paper ? await patchWorksheet(paper.id, { ...fields, etag: paper.etag }) : await createWorksheet(fields);
      updatePaper(value);
      if (alive.current) { setSettingsOpen(false); setActiveId(activeId || value.questions?.[0]?.id || ""); }
    } catch (error) { report(error); }
    finally { if (alive.current) setBusy(false); }
  }

  async function generateQuestions(count: number, hint = generation.hint) {
    if (!paper || disabled || !validScope) return;
    setBusy(true);
    try {
      const value = await generateWorksheet(paper.id, {
        count, question_type: generation.type, difficulty: Number(generation.difficulty), score: Number(generation.score),
        knowledge_points: parsePoints(generation.points), guidance_prompt: hint.trim(),
        idempotency_key: crypto.randomUUID(),
      });
      updatePaper(value.worksheet);
      if (!alive.current) return;
      const question = value.worksheet.questions?.at(-1);
      if (question) {
        setActiveId(question.id); setComposeMode("revise");
        setMessages((previous) => ({ ...previous, [question.id]: [
          ...(hint.trim() ? [{ role: "user" as const, text: hint.trim() }] : []),
          { role: "assistant", text: tr.generated + " · " + question.number + ". " + tr.conversationHint },
        ] }));
      }
      setInstruction(""); setGenerationMode(null);
      if (value.errors?.some((error) => error.includes("draft_fallback"))) toast(tr.fallback, "error");
      else if (value.errors?.length) toast(tr.partialGenerated, "error");
      else toast(tr.savedToast);
    } catch (error) { report(error); }
    finally { if (alive.current) setBusy(false); }
  }

  async function sendInstruction(event: FormEvent) {
    event.preventDefault();
    const value = instruction.trim();
    if (!value || !paper || disabled) return;
    if (composeMode === "new" || !active) { await generateQuestions(1, value); return; }
    const key = active.id;
    setBusy(true);
    setMessages((previous) => ({ ...previous, [key]: [...(previous[key] ?? []), { role: "user", text: value }] }));
    try {
      const result = await refineWorksheetQuestion(paper.id, key, { instruction: value, etag: paper.etag });
      updatePaper(result);
      if (alive.current) {
        setInstruction("");
        setMessages((previous) => ({ ...previous, [key]: [...(previous[key] ?? []), { role: "assistant", text: tr.reviseDone }] }));
      }
    } catch (error) { report(error); }
    finally { if (alive.current) setBusy(false); }
  }

  async function saveQuestion(fields: Omit<WorksheetQuestionPatchRequest, "etag">) {
    if (!paper || !active || disabled) return;
    setBusy(true);
    try {
      const value = await patchWorksheetQuestion(paper.id, active.id, { ...fields, etag: paper.etag });
      updatePaper(value);
      if (alive.current) { setEditorOpen(false); toast(tr.savedToast); }
    } catch (error) { report(error); }
    finally { if (alive.current) setBusy(false); }
  }

  async function remove() {
    if (!paper || disabled) return;
    setBusy(true);
    try {
      if (pendingDelete === "question" && active) {
        const index = questions.findIndex((question) => question.id === active.id);
        const value = await deleteWorksheetQuestion(paper.id, active.id, paper.etag);
        updatePaper(value);
        if (alive.current) { setActiveId(value.questions?.[Math.max(0, index - 1)]?.id ?? ""); setInstruction(""); }
      } else {
        await deleteWorksheet(paper.id);
        if (alive.current) { setDrafts((previous) => previous.filter((item) => item.id !== paper.id)); setPaper(null); setActiveId(""); setMessages({}); }
      }
      if (alive.current) setPendingDelete(null);
    } catch (error) { report(error); }
    finally { if (alive.current) setBusy(false); }
  }

  async function attachGeneratedImage(result: ImageGenerationResult) {
    if (!paper || !active) return;
    if (!result.image_url.startsWith("data:image/")) { toast(tr.imageUnavailable, "error"); return; }
    setBusy(true);
    try {
      const value = await attachWorksheetImage(paper.id, active.id, { data_url: result.image_url, alt: active.stem.slice(0, 160), etag: paper.etag });
      updatePaper(value);
      if (alive.current) { setImageOpen(false); toast(tr.savedToast); }
    } catch (error) { report(error); }
    finally { if (alive.current) setBusy(false); }
  }

  async function exportPaper(format: "markdown" | "html", print = false) {
    if (!paper || disabled) return;
    setExportBusy(true);
    try {
      const variant = paperAnswers ? "teacher" : "student";
      const value = await exportWorksheet(paper.id, variant, format);
      if (print) await printWorksheetHtml(value.content);
      else download(value.content, safeName(paper.title) + "-" + variant + (format === "html" ? ".html" : ".md"), format === "html" ? "text/html;charset=utf-8" : "text/markdown;charset=utf-8");
    } catch (error) { report(error); }
    finally { if (alive.current) setExportBusy(false); }
  }

  if (loading) return <div className="h-full p-6"><p className="mb-5 text-sm text-muted">{tr.loading}</p><div className="skeleton h-16 w-full" /><div className="mt-5 grid gap-5 md:grid-cols-3">{[0, 1, 2].map((index) => <div key={index} className="skeleton h-80" />)}</div></div>;
  if (loadError) return <div className="flex h-full flex-col items-center justify-center gap-4"><p className="text-sm text-muted">{tr.loadFailed}</p><Button onClick={() => window.location.reload()}>{tr.retry}</Button></div>;

  return (
    <div className="h-full overflow-y-auto bg-bg p-4 sm:p-6">
      <div className="worksheet-container mx-auto max-w-[1480px]">
        {!paper ? <DraftLobby tr={tr} drafts={drafts} onOpen={openDraft} onNew={() => { setSettingsOpen(true); }} /> : (
          <div className="space-y-4">
            <header className="flex flex-wrap items-start justify-between gap-4">
              <div className="flex min-w-0 items-start gap-3">
                <Link href="/tools" aria-label={tr.back} className="mt-1 rounded-[8px] p-1.5 text-muted hover:bg-surface-hover hover:text-accent"><BackMark className="h-5 w-5" /></Link>
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[12px] bg-accent2-soft text-accent2-strong"><PaperCompilerMark className="h-7 w-7" /></span>
                <div className="min-w-0"><h1 className="max-w-[34ch] break-words font-serif text-2xl font-semibold tracking-tight text-fg">{paper.title}</h1><p className="mt-1 text-xs text-muted">{activeArea?.name || paper.learning_area || tr.learningArea} · {questions.length} {tr.questions} · {paper.total_score ?? 0} {tr.pointsUnit}</p></div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" icon={<ComposeMark className="h-4 w-4" />} onClick={() => setGenerationMode("batch")} disabled={disabled || !validScope}>{tr.batchGenerate}</Button>
                <Button size="sm" variant="outline" icon={<PaperCompilerMark className="h-4 w-4" />} onClick={() => setPaperOpen(true)} disabled={disabled || !questions.length}>{tr.wholePreview}</Button>
                <Button size="sm" icon={<ExportMark className="h-4 w-4" />} onClick={() => void exportPaper("html", true)} disabled={disabled || !questions.length}>{exportBusy ? tr.preparingPrint : tr.print}</Button>
                <Button size="sm" variant="ghost" onClick={() => setSettingsOpen(true)} disabled={disabled}>{tr.setup}</Button>
                <Button size="sm" variant="ghost" onClick={() => { setPaper(null); setActiveId(""); setMessages({}); }} disabled={disabled}>{tr.list}</Button>
              </div>
            </header>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border pb-4 text-xs text-fg-secondary">
              <span>{tr.pointsSummary} · {(paper.knowledge_points ?? []).join("、") || "—"}</span>
              <span className={paper.reference_textbook && paper.knowledge_points?.length ? "text-accent-strong" : "text-muted"}>{paper.reference_textbook && paper.knowledge_points?.length ? tr.textbookOn : tr.textbookOff}</span>
              <span className="ml-auto text-muted">{tr.status}</span>
            </div>
            {areasReady && !validScope && <div role="status" className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-danger/30 px-4 py-3 text-sm text-danger"><span>{tr.areaMissing}</span><Button size="sm" variant="outline" onClick={() => setSettingsOpen(true)}>{tr.setup}</Button></div>}
            <div className="worksheet-layout">
              <QuestionRail questions={questions} activeId={activeId} tr={tr} disabled={disabled || !validScope} onSelect={(id) => { setActiveId(id); setInstruction(""); setComposeMode("revise"); }} onAdd={() => setGenerationMode("single")} onDelete={() => setPendingDelete("paper")} />
              <section className="worksheet-chat flex min-w-0 flex-col rounded-[14px] border border-border bg-surface" aria-label={tr.conversation}>
                <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
                  <div><h2 className="text-sm font-semibold text-fg">{tr.conversation}</h2><p className="mt-1 text-xs text-muted">{active ? tr.selectedQuestion + " · " + active.number : tr.conversationEmpty}</p></div>
                  {active && <Button size="sm" variant="outline" icon={<RevisionMark className="h-4 w-4" />} onClick={() => setEditorOpen(true)} disabled={disabled}>{tr.editor}</Button>}
                </header>
                <ChatMessages messages={chat} tr={tr} busy={busy} hasQuestion={Boolean(active)} />
                <form onSubmit={sendInstruction} className="shrink-0 border-t border-border p-4 sm:p-5">
                  <div className="mb-3 flex flex-wrap gap-2">
                    <Button type="button" size="sm" variant="ghost" selected={composeMode === "revise"} disabled={!active || disabled} onClick={() => setComposeMode("revise")}>{tr.reviseMode}</Button>
                    <Button type="button" size="sm" variant="ghost" selected={composeMode === "new"} disabled={disabled || !validScope} onClick={() => setComposeMode("new")}>{tr.newMode}</Button>
                  </div>
                  <Textarea aria-label={composeMode === "new" ? tr.generationHint : tr.instruction} value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={3} maxLength={2000} disabled={disabled || !validScope} placeholder={composeMode === "new" ? tr.newPlaceholder : tr.refinePlaceholder} />
                  <div className="mt-3 flex items-center justify-between gap-3"><span className="text-[11px] text-muted">{tr.chatLocal}</span><Button type="submit" size="sm" disabled={disabled || !instruction.trim() || !validScope} icon={<ComposeMark className="h-4 w-4" />}>{busy ? tr.generating : composeMode === "new" || !active ? tr.sendNew : tr.apply}</Button></div>
                </form>
              </section>
              <section className="worksheet-preview flex min-w-0 flex-col rounded-[14px] border border-border bg-surface-sunken/40" aria-label={tr.preview}>
                <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
                  <div><h2 className="text-sm font-semibold text-fg">{tr.preview}</h2><p className="mt-1 text-xs text-muted">{active ? tr[active.type] + " · " + active.score + " " + tr.pointsUnit : tr.select}</p></div>
                  <Button size="sm" variant="ghost" onClick={() => setShowAnswers((previous) => !previous)}>{showAnswers ? tr.hideAnswer : tr.showAnswer}</Button>
                </header>
                <div className="min-h-0 flex-1 overflow-y-auto"><SingleQuestionPreview question={active} showAnswers={showAnswers} tr={tr} /></div>
                {active && <footer className="flex flex-wrap gap-2 border-t border-border p-3"><Button size="sm" variant="ghost" onClick={() => setImageOpen(true)} disabled={disabled} icon={<ComposeMark className="h-4 w-4" />}>{tr.generateImage}</Button><Button size="sm" variant="ghost" tone="danger" onClick={() => setPendingDelete("question")} disabled={disabled}>{tr.delete}</Button></footer>}
              </section>
            </div>
          </div>
        )}
      </div>
      {settingsOpen && <SettingsModal key={paper?.id ?? "new"} paper={paper} areas={areas} areasReady={areasReady} areaError={areaError} tr={tr} busy={busy} onClose={() => { if (!busy) setSettingsOpen(false); }} onReloadAreas={reloadAreas} onSave={saveSettings} />}
      {generationMode && paper && <GenerationModal mode={generationMode} values={generation} setValues={setGeneration} tr={tr} busy={busy} points={paper.knowledge_points ?? []} onClose={() => { if (!busy) setGenerationMode(null); }} onGenerate={(count) => void generateQuestions(count)} />}
      {editorOpen && active && <QuestionEditor key={active.id} question={active} tr={tr} busy={busy} onClose={() => { if (!busy) setEditorOpen(false); }} onSave={saveQuestion} />}
      {imageOpen && active && <Modal open width={860} onClose={() => { if (!busy) setImageOpen(false); }} title={tr.generateImage}><ImageGenerationStudio initialPrompt={active.stem} showBackLink={false} onGenerated={(result) => void attachGeneratedImage(result)} /></Modal>}
      {pendingDelete && <ConfirmModal open onClose={() => { if (!busy) setPendingDelete(null); }} onConfirm={() => void remove()} title={pendingDelete === "paper" ? tr.deleteSheet : tr.delete} desc={pendingDelete === "paper" ? tr.deleteWarning : tr.deleteQuestionWarning} confirmText={pendingDelete === "paper" ? tr.deleteSheet : tr.delete} cancelText={tr.cancel} />}
      {paperOpen && paper && <Modal open width={1000} onClose={() => setPaperOpen(false)} title={tr.wholePreview}>
        <div className="sticky top-0 z-10 mb-4 space-y-3 bg-surface pb-4">
          <p className="text-xs leading-5 text-muted">{tr.paperPreviewHint}</p>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex gap-1"><Button size="sm" variant="ghost" selected={!paperAnswers} onClick={() => setPaperAnswers(false)}>{tr.studentVersion}</Button><Button size="sm" variant="ghost" selected={paperAnswers} onClick={() => setPaperAnswers(true)}>{tr.teacherVersion}</Button></div>
            <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={disabled} onClick={() => void exportPaper("markdown")}>{tr.exportMarkdown}</Button><Button size="sm" variant="outline" disabled={disabled} onClick={() => void exportPaper("html")}>{tr.exportHtml}</Button><Button size="sm" disabled={disabled} onClick={() => void exportPaper("html", true)} icon={<ExportMark className="h-4 w-4" />}>{exportBusy ? tr.preparingPrint : tr.print}</Button><Button size="sm" variant="ghost" onClick={() => setPaperOpen(false)}>{tr.close}</Button></div>
          </div>
        </div>
        <PaperPreview document={paper} showAnswers={paperAnswers} tr={tr} />
      </Modal>}
    </div>
  );
}

function DraftLobby({ tr, drafts, onNew, onOpen }: { tr: WorksheetCopy; drafts: Draft[]; onNew: () => void; onOpen: (id: string) => void }) {
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const rows = drafts.filter((draft) => draft.title.toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <div className="mx-auto max-w-[1050px] py-4 sm:py-8">
      <Link href="/tools" className="inline-flex items-center gap-2 rounded-[8px] p-1 text-xs text-muted hover:text-accent"><BackMark className="h-4 w-4" />{tr.back}</Link>
      <div className="mt-8 grid gap-8 md:grid-cols-[0.8fr_1.2fr] md:gap-14">
        <div>
          <PaperCompilerMark className="h-16 w-16 text-accent2-strong" />
          <h1 className="mt-5 font-serif text-3xl font-semibold tracking-tight text-fg">{tr.title}</h1>
          <p className="mt-4 max-w-[38ch] text-sm leading-7 text-fg-secondary">{tr.intro}</p>
          <Button className="mt-6" size="lg" onClick={onNew} icon={<ComposeMark className="h-5 w-5" />}>{tr.new}</Button>
          <div className="mt-10 border-t border-border pt-5"><h2 className="text-sm font-medium text-fg">{tr.paperStyle}</h2><p className="mt-2 text-xs leading-6 text-muted">{tr.paperStyleDesc}</p></div>
        </div>
        <section className="min-w-0">
          <div className="mb-4 flex items-center justify-between gap-4"><h2 className="text-base font-semibold text-fg">{tr.saved}</h2><span className="tnum text-xs text-muted">{drafts.length}</span></div>
          {drafts.length > 5 && <Input aria-label={tr.searchDraft} value={query} placeholder={tr.searchDraft} onChange={(event) => { setQuery(event.target.value); setPage(0); }} className="mb-4" />}
          {rows.length ? <div className="divide-y divide-border rounded-[14px] border border-border bg-surface">{paged(rows, page).map((draft) => <button key={draft.id} type="button" onClick={() => onOpen(draft.id)} className="flex w-full items-center justify-between gap-4 px-5 py-5 text-left transition-colors first:rounded-t-[14px] last:rounded-b-[14px] hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-accent"><div className="min-w-0"><h3 className="truncate text-sm font-medium text-fg">{draft.title}</h3><p className="mt-1.5 text-xs text-muted">{draft.question_count} {tr.questions} · {draft.total_score} {tr.pointsUnit}</p></div><span className="shrink-0 text-xs text-accent-strong">{tr.resume}</span></button>)}</div> : <div className="rounded-[14px] border border-dashed border-border px-5 py-16 text-center text-sm text-muted">{drafts.length ? tr.noMatches : tr.empty}</div>}
          <Pager page={page} total={rows.length} onPage={setPage} />
        </section>
      </div>
    </div>
  );
}

function QuestionRail({ questions, activeId, tr, disabled, onSelect, onAdd, onDelete }: { questions: WorksheetQuestion[]; activeId: string; tr: WorksheetCopy; disabled: boolean; onSelect: (id: string) => void; onAdd: () => void; onDelete: () => void }) {
  const [page, setPage] = useState<number | null>(null);
  const currentPage = page ?? Math.floor(Math.max(0, questions.findIndex((question) => question.id === activeId)) / 8);
  return <nav className="worksheet-rail flex min-w-0 flex-col rounded-[14px] border border-border bg-surface" aria-label={tr.questions}>
    <header className="border-b border-border p-4"><div className="flex items-baseline justify-between gap-2"><h2 className="text-sm font-semibold text-fg">{tr.questions}</h2><span className="tnum text-xs text-muted">{questions.length}</span></div><Button className="mt-3 w-full" size="sm" disabled={disabled} onClick={onAdd} icon={<ComposeMark className="h-4 w-4" />}>{tr.singleGenerate}</Button></header>
    <div className="min-h-0 flex-1 overflow-y-auto p-2">
      {questions.length ? paged(questions, currentPage, 8).map((question) => <button key={question.id} type="button" disabled={disabled} aria-current={question.id === activeId ? "true" : undefined} onClick={() => { setPage(null); onSelect(question.id); }} className={"mb-1 flex w-full items-start gap-2.5 rounded-[10px] border p-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-60 " + (question.id === activeId ? "border-accent/40 bg-accent-soft" : "border-transparent hover:bg-surface-hover")}>
        <span className="tnum shrink-0 text-sm font-semibold text-accent-strong">{String(question.number).padStart(2, "0")}</span><div className="min-w-0"><span className="line-clamp-2 break-words text-xs leading-5 text-fg">{question.stem}</span><span className="mt-1 block text-[11px] text-muted">{tr[question.type]} · {question.score} {tr.pointsUnit}</span></div>
      </button>) : <p className="px-2 py-7 text-xs leading-6 text-muted">{tr.noQuestions}</p>}
    </div>
    <div className="border-t border-border p-3"><Pager page={currentPage} total={questions.length} per={8} onPage={setPage} /><Button size="sm" variant="ghost" tone="danger" disabled={disabled} onClick={onDelete}>{tr.deleteSheet}</Button></div>
  </nav>;
}

function ChatMessages({ messages, tr, busy, hasQuestion }: { messages: Message[]; tr: WorksheetCopy; busy: boolean; hasQuestion: boolean }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest", behavior: "instant" }); }, [messages.length, busy]);
  return <div className="min-h-[260px] flex-1 space-y-5 overflow-y-auto p-5" aria-live="polite" aria-busy={busy}>
    {!messages.length && <div className="max-w-[60ch] space-y-4 pt-3"><ComposeMark className="h-8 w-8 text-accent" /><p className="text-sm leading-7 text-fg-secondary">{hasQuestion ? tr.reviseStart : tr.conversationEmpty}</p>{hasQuestion && <p className="text-xs leading-6 text-muted">{tr.conversationHint}</p>}</div>}
    {messages.map((message, index) => <div key={index} className={message.role === "user" ? "ml-8 rounded-[12px] bg-accent-soft px-4 py-3" : "mr-4"}><p className="mb-1.5 text-[11px] font-semibold text-muted">{message.role === "user" ? tr.you : tr.assistant}</p><Markdown className="chat-prose text-sm">{message.text}</Markdown></div>)}
    {busy && <div className="flex items-center gap-3 text-xs text-muted"><span className="dot-loader" aria-hidden="true"><span /><span /><span /></span>{tr.generating}</div>}
    <div ref={end} />
  </div>;
}

function SettingsModal({ paper, areas, areasReady, areaError, tr, busy, onClose, onReloadAreas, onSave }: { paper: WorksheetDocument | null; areas: WorkspaceItem[]; areasReady: boolean; areaError: boolean; tr: WorksheetCopy; busy: boolean; onClose: () => void; onReloadAreas: () => void; onSave: (fields: WorksheetCreateRequest) => void }) {
  const [step, setStep] = useState(1);
  const [values, setValues] = useState({
    title: paper?.title ?? "", workspaceId: paper?.workspace_id ?? "", subject: paper?.subject ?? "", grade: paper?.grade ?? "",
    unit: paper?.unit ?? "", duration: String(paper?.duration_minutes ?? 45), goal: paper?.goal ?? "", points: (paper?.knowledge_points ?? []).join("、"),
    instructions: paper?.instructions ?? "", guidance: paper?.guidance_prompt ?? "", reference: Boolean(paper?.reference_textbook),
  });
  const area = areas.find((item) => item.workspace_id === values.workspaceId);
  const points = parsePoints(values.points);
  const hasTextbooks = Boolean(area?.selected_file_ids?.length);
  const field = (key: keyof typeof values, value: string | boolean) => setValues((previous) => ({ ...previous, [key]: value }));
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!area || !values.title.trim() || busy) return;
    if (step === 1) { setStep(2); return; }
    onSave({ title: values.title.trim(), workspace_id: area.workspace_id, learning_area: area.name, subject: values.subject, grade: values.grade, unit: values.unit, duration_minutes: Number(values.duration), goal: values.goal, knowledge_points: points, reference_textbook: values.reference && points.length > 0 && hasTextbooks, instructions: values.instructions, guidance_prompt: values.guidance });
  }
  return <Modal open width={760} onClose={onClose} title={paper ? tr.setup : tr.new}>
    <form onSubmit={submit} className="space-y-5">
      <div className="flex gap-5 border-b border-border pb-4"><button type="button" onClick={() => setStep(1)} className={"text-sm " + (step === 1 ? "font-semibold text-accent-strong" : "text-muted")}>1 · {tr.stepScope}</button><button type="button" disabled={!area || !values.title.trim()} onClick={() => setStep(2)} className={"text-sm disabled:opacity-50 " + (step === 2 ? "font-semibold text-accent-strong" : "text-muted")}>2 · {tr.stepGuidance}</button></div>
      {step === 1 ? <>
        <Field label={tr.titleField}><Input aria-label={tr.titleField} required maxLength={120} value={values.title} onChange={(event) => field("title", event.target.value)} /></Field>
        <Field label={tr.learningArea + " *"}><select aria-label={tr.learningArea} required className={FIELD_CLS} disabled={!areasReady} value={area ? values.workspaceId : ""} onChange={(event) => setValues((previous) => ({ ...previous, workspaceId: event.target.value, points: "", reference: false }))}><option value="">{tr.learningAreaRequired}</option>{areas.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>)}</select>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs"><span className="text-muted">{!areasReady ? tr.loading : areaError ? tr.loadFailed : !areas.length ? tr.learningAreaEmpty : tr.setupHint}</span><Button type="button" size="sm" variant="ghost" onClick={() => useWsSettings.getState().open("new")}>{tr.createArea}</Button>{areaError && <Button type="button" size="sm" variant="outline" onClick={onReloadAreas}>{tr.retry}</Button>}</div>
        </Field>
        <div className="grid gap-4 sm:grid-cols-2"><Field label={tr.subject}><Input aria-label={tr.subject} maxLength={60} value={values.subject} onChange={(event) => field("subject", event.target.value)} /></Field><Field label={tr.grade}><Input aria-label={tr.grade} maxLength={60} value={values.grade} onChange={(event) => field("grade", event.target.value)} /></Field><Field label={tr.unit}><Input aria-label={tr.unit} maxLength={120} value={values.unit} onChange={(event) => field("unit", event.target.value)} /></Field><Field label={tr.duration}><Input aria-label={tr.duration} type="number" required min={1} max={600} value={values.duration} onChange={(event) => field("duration", event.target.value)} /></Field></div>
      </> : <>
        <Field label={tr.goal}><Textarea aria-label={tr.goal} rows={3} maxLength={4000} value={values.goal} onChange={(event) => field("goal", event.target.value)} placeholder={tr.goalPlaceholder} /></Field>
        <KnowledgePointPicker key={values.workspaceId} workspaceId={values.workspaceId} points={points} tr={tr} onChange={(next) => setValues((previous) => ({ ...previous, points: next.join("、"), reference: next.length > 0 && previous.reference }))} />
        <Field label={tr.knowledgeGraph}><Textarea aria-label={tr.knowledgeGraph} rows={2} value={values.points} onChange={(event) => { const value = event.target.value; setValues((previous) => ({ ...previous, points: value, reference: parsePoints(value).length > 0 && previous.reference })); }} placeholder={tr.knowledgePlaceholder} /></Field>
        <label className="flex items-start gap-3 rounded-[10px] border border-border p-3"><Input type="checkbox" disabled={!points.length || !hasTextbooks} checked={values.reference && points.length > 0 && hasTextbooks} onChange={(event) => field("reference", event.target.checked)} className="mt-0.5" /><span className="min-w-0"><span className="block text-sm font-medium text-fg">{tr.textbook}</span><span className="mt-1 block text-xs leading-5 text-muted">{hasTextbooks ? tr.textbookHint : tr.noTextbooks}</span></span></label>
        <Button type="button" size="sm" variant="ghost" onClick={() => useWsSettings.getState().open(values.workspaceId)}>{tr.manageArea}</Button>
        <div className="grid gap-4 sm:grid-cols-2"><Field label={tr.instructions}><Textarea aria-label={tr.instructions} rows={3} maxLength={2000} value={values.instructions} onChange={(event) => field("instructions", event.target.value)} /></Field><Field label={tr.guidance}><Textarea aria-label={tr.guidance} rows={3} maxLength={4000} value={values.guidance} onChange={(event) => field("guidance", event.target.value)} /></Field></div>
      </>}
      <div className="flex flex-wrap justify-between gap-2 border-t border-border pt-4">
        <Button type="button" variant="ghost" disabled={busy} onClick={step === 1 ? onClose : () => setStep(1)}>{step === 1 ? tr.cancel : tr.previous}</Button>
        <Button type="submit" disabled={busy || !area || !values.title.trim()} icon={<ComposeMark className="h-4 w-4" />}>{busy ? tr.generating : step === 1 ? tr.continue : paper ? tr.saveSettings : tr.create}</Button>
      </div>
    </form>
  </Modal>;
}

function KnowledgePointPicker({ workspaceId, points, tr, onChange }: { workspaceId: string; points: string[]; tr: WorksheetCopy; onChange: (points: string[]) => void }) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ items: ConceptEvaluationView[]; total: number; key: string; error: boolean } | null>(null);
  const requestKey = workspaceId + ":" + query + ":" + page + ":" + retry;
  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      getEvalConcepts(workspaceId, { q: query.trim() || undefined, offset: page * 12, limit: 12 }).then((value) => {
        if (!cancelled) setResult({ items: value.items, total: value.total, key: requestKey, error: false });
      }).catch(() => { if (!cancelled) setResult({ items: [], total: 0, key: requestKey, error: true }); });
    }, query ? 250 : 0);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [workspaceId, query, page, retry, requestKey]);
  const pending = result?.key !== requestKey;
  return <section aria-label={tr.knowledgeGraph} className="space-y-3">
    <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-semibold text-fg">{tr.knowledgeGraph}</h3><span className="tnum text-xs text-muted">{tr.selectedCount} {points.length}/12</span></div>
    <Input type="search" aria-label={tr.knowledgeSearch} value={query} placeholder={tr.knowledgeSearch} onChange={(event) => { setQuery(event.target.value); setPage(0); }} />
    {points.length > 0 && <div className="flex flex-wrap items-center gap-1.5">{points.map((point) => <button type="button" key={point} aria-label={tr.delete + " " + point} onClick={() => onChange(points.filter((item) => item !== point))} className="rounded-[7px] border border-accent/30 bg-accent-soft px-2.5 py-1.5 text-xs text-accent-strong hover:border-accent">{point} <span aria-hidden="true">×</span></button>)}<Button type="button" size="sm" variant="ghost" onClick={() => onChange([])}>{tr.clearPoints}</Button></div>}
    <div className="min-h-[88px] rounded-[10px] border border-border bg-surface-sunken/30 p-3">
      {pending ? <p className="py-3 text-xs text-muted">{tr.conceptsLoading}</p> : result?.error ? <div className="space-y-2"><p className="text-xs text-muted">{tr.conceptsFailed}</p><Button type="button" size="sm" variant="outline" onClick={() => setRetry((previous) => previous + 1)}>{tr.retry}</Button></div> : result?.items.length ? <div className="flex flex-wrap gap-2">{result.items.map((concept) => {
        const label = concept.concept_ref.display_name;
        const selected = points.includes(label);
        return <button key={concept.concept_ref.key} type="button" aria-pressed={selected} disabled={!selected && points.length >= 12} onClick={() => onChange(selected ? points.filter((point) => point !== label) : [...points, label])} className={"rounded-[7px] border px-2.5 py-1.5 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50 " + (selected ? "border-accent/40 bg-accent-soft text-accent-strong" : "border-border bg-surface text-fg-secondary hover:border-accent")}>{label}</button>;
      })}</div> : <p className="py-3 text-xs leading-6 text-muted">{query ? tr.noMatches : tr.noConcepts}</p>}
    </div>
    {!pending && <Pager page={page} total={result?.total ?? 0} per={12} onPage={setPage} />}
  </section>;
}

function GenerationModal({ mode, values, setValues, tr, busy, points, onClose, onGenerate }: { mode: "single" | "batch"; values: GenerationValues; setValues: (values: GenerationValues) => void; tr: WorksheetCopy; busy: boolean; points: string[]; onClose: () => void; onGenerate: (count: number) => void }) {
  function submit(event: FormEvent) { event.preventDefault(); if (!busy) onGenerate(mode === "single" ? 1 : Number(values.count)); }
  return <Modal open width={620} onClose={onClose} title={mode === "single" ? tr.singleGenerate : tr.batchGenerate}>
    <form onSubmit={submit} className="space-y-4">
      <p className="text-xs leading-6 text-muted">{mode === "single" ? tr.setupHint : tr.batchHint}</p>
      <div className="grid gap-4 sm:grid-cols-2">
        {mode === "batch" && <Field label={tr.count}><Input aria-label={tr.count} required type="number" min={1} max={50} value={values.count} onChange={(event) => setValues({ ...values, count: event.target.value })} /></Field>}
        <Field label={tr.type}><select aria-label={tr.type} className={FIELD_CLS} value={values.type} onChange={(event) => setValues({ ...values, type: event.target.value as WorksheetQuestion["type"] })}>{(["multiple_choice", "fill_blank", "short_answer"] as const).map((type) => <option key={type} value={type}>{tr[type]}</option>)}</select></Field>
        <Field label={tr.difficulty}><Input aria-label={tr.difficulty} type="number" required min={1} max={5} value={values.difficulty} onChange={(event) => setValues({ ...values, difficulty: event.target.value })} /></Field>
        <Field label={tr.score}><Input aria-label={tr.score} type="number" required min={0} max={100} value={values.score} onChange={(event) => setValues({ ...values, score: event.target.value })} /></Field>
      </div>
      <Field label={tr.points}><Input aria-label={tr.points} value={values.points} onChange={(event) => setValues({ ...values, points: event.target.value })} placeholder={points.join("、") || tr.knowledgePlaceholder} /></Field>
      <Field label={tr.generationHint}><Textarea aria-label={tr.generationHint} rows={3} maxLength={2000} value={values.hint} onChange={(event) => setValues({ ...values, hint: event.target.value })} placeholder={tr.newPlaceholder} /></Field>
      <div className="flex justify-end gap-2 border-t border-border pt-4"><Button type="button" variant="ghost" disabled={busy} onClick={onClose}>{tr.cancel}</Button><Button type="submit" disabled={busy} icon={<ComposeMark className="h-4 w-4" />}>{busy ? tr.generating : mode === "single" ? tr.add : tr.generate}</Button></div>
    </form>
  </Modal>;
}

function QuestionEditor({ question, tr, busy, onClose, onSave }: { question: WorksheetQuestion; tr: WorksheetCopy; busy: boolean; onClose: () => void; onSave: (fields: Omit<WorksheetQuestionPatchRequest, "etag">) => void }) {
  const [values, setValues] = useState({ stem: question.stem, answer: question.answer ?? "", explanation: question.explanation ?? "", score: String(question.score ?? 5), difficulty: String(question.difficulty ?? 3), points: (question.knowledge_points ?? []).join("、"), options: Object.entries(question.options ?? {}).map(([key, value]) => key + ": " + value).join("\n") });
  const field = (key: keyof typeof values, value: string) => setValues((previous) => ({ ...previous, [key]: value }));
  function submit(event: FormEvent) { event.preventDefault(); if (!busy) onSave({ stem: values.stem, answer: values.answer, explanation: values.explanation, score: Number(values.score), difficulty: Number(values.difficulty), knowledge_points: parsePoints(values.points, 8), options: question.type === "multiple_choice" ? parseOptions(values.options) : {} }); }
  return <Modal open width={800} onClose={onClose} title={tr.editor + " · " + question.number}>
    <form onSubmit={submit} className="space-y-4">
      <p className="text-xs leading-6 text-muted">{tr.editHint}</p>
      <div className="grid gap-4 sm:grid-cols-3"><div><p className="mb-1.5 text-xs text-fg-secondary">{tr.type}</p><p className="py-2 text-sm">{tr[question.type]}</p></div><Field label={tr.score}><Input aria-label={tr.score} required type="number" min={0} max={100} value={values.score} onChange={(event) => field("score", event.target.value)} /></Field><Field label={tr.difficulty}><Input aria-label={tr.difficulty} required type="number" min={1} max={5} value={values.difficulty} onChange={(event) => field("difficulty", event.target.value)} /></Field></div>
      <Field label={tr.stem}><Textarea aria-label={tr.stem} required rows={5} maxLength={12000} value={values.stem} onChange={(event) => field("stem", event.target.value)} /><p className="mt-2 text-xs leading-5 text-muted">{tr.mathHint}</p></Field>
      {question.type === "multiple_choice" && <Field label={tr.options}><Textarea aria-label={tr.options} rows={4} value={values.options} onChange={(event) => field("options", event.target.value)} /></Field>}
      <Field label={tr.answerField}><Textarea aria-label={tr.answerField} rows={2} maxLength={6000} value={values.answer} onChange={(event) => field("answer", event.target.value)} /></Field>
      <Field label={tr.explanation}><Textarea aria-label={tr.explanation} rows={4} maxLength={8000} value={values.explanation} onChange={(event) => field("explanation", event.target.value)} /></Field>
      <Field label={tr.knowledge}><Input aria-label={tr.knowledge} value={values.points} onChange={(event) => field("points", event.target.value)} /></Field>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"><span className="text-xs text-muted">{tr.saveHint}</span><div className="flex gap-2"><Button type="button" variant="ghost" disabled={busy} onClick={onClose}>{tr.cancel}</Button><Button type="submit" disabled={busy}>{busy ? tr.generating : tr.save}</Button></div></div>
    </form>
  </Modal>;
}

function parsePoints(value: string, limit = 12): string[] { return [...new Set(value.split(/[、,，\n]/).map((item) => item.trim().slice(0, 120)).filter(Boolean))].slice(0, limit); }
function parseOptions(value: string): Record<string, string> {
  return Object.fromEntries(value.split("\n").flatMap((line) => { const match = line.match(/^\s*([^:：]+)[:：]\s*(.*)$/); return match ? [[match[1].trim(), match[2]]] : []; }));
}
function download(content: string, filename: string, type: string) { const url = URL.createObjectURL(new Blob([content], { type })); const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
function safeName(value: string) { return value.replace(/[\\/:*?"<>|]/g, "-").trim() || "worksheet"; }
