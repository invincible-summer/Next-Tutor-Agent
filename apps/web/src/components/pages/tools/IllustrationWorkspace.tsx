"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Download, ImagePlus, LoaderCircle, Plus, Send, Sparkles, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Field, FIELD_CLS, Textarea } from "@/components/ui/Input";
import { ConfirmModal } from "@/components/ui/Modal";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { QuestionIllustration } from "@/components/quiz/QuestionIllustration";
import { useUIStore } from "@/lib/store";
import { useAuthStore } from "@/lib/auth-store";
import { makePageT } from "@/lib/i18n-page";
import { DEMO_MODE, demoReadOnly } from "@/lib/demo";
import { illustrationFailure, illustrationMode, type IllustrationMode } from "@/lib/api-illustrations";
import {
  createIllustrationSession, deleteIllustrationSession, getIllustrationSession, getToolIllustrationJob,
  listIllustrationSessions, retryToolIllustrationJob, startIllustrationTurn, IllustrationToolError,
  type IllustrationSession, type IllustrationSessionSummary, type ToolIllustrationJob,
} from "@/lib/api-illustration-tools";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { MaterialPicker, type SelectedMaterial } from "./MaterialPicker";
import { STRINGS } from "@/app/(workspace)/tools/strings";

export function IllustrationWorkspace() {
  const owner = useAuthStore(s => s.user?.id ?? "guest");
  return <OwnedIllustrationWorkspace key={owner} />;
}

function OwnedIllustrationWorkspace() {
  const lang = useUIStore(s => s.lang);
  const preferredMode = useAuthStore(s => s.user?.profile?.prefs?.quiz_illustration_mode);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const router = useRouter();
  const query = useSearchParams();
  const sessionId = query.get("session") ?? "";
  const [history, setHistory] = useState<IllustrationSessionSummary[]>([]);
  const [historyPage, setHistoryPage] = useState(0);
  const [historyError, setHistoryError] = useState(false);
  const [session, setSession] = useState<IllustrationSession | null>(null);
  const [loading, setLoading] = useState(!DEMO_MODE);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<IllustrationMode>(() => illustrationMode(preferredMode));
  const [materials, setMaterials] = useState<SelectedMaterial[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [activeJobId, setActiveJobId] = useState("");
  const [job, setJob] = useState<ToolIllustrationJob | null>(null);
  const [viewRevision, setViewRevision] = useState(0);
  const [sourceRevision, setSourceRevision] = useState<number | null>(null);
  const [observationStopped, setObservationStopped] = useState<{ jobId: string; retryable: boolean } | null>(null);
  const [revisionPage, setRevisionPage] = useState(0);
  const [deleting, setDeleting] = useState<IllustrationSessionSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const generation = useRef(0);
  const operation = useRef<AbortController | null>(null);
  const messagesEnd = useRef<HTMLDivElement>(null);
  const loadedPath = useRef<string | null>(null);
  const pendingSubmission = useRef<{ fingerprint: string; requestId: string; sessionId: string | null; baseRevision: number; sourceRevision: number } | null>(null);

  useAssistantPage({
    context: () => ({ schema_version: 1, route_id: "tools_illustration", route_epoch: currentRouteEpoch() }),
    clientState: () => ({ dirty: !!draft.trim(), blocking_activity: draft.trim() ? "unsaved_editor" : "none", safe_bottom_px: 24 }),
  });

  const refreshHistory = useCallback(async (signal?: AbortSignal) => {
    if (DEMO_MODE) return;
    try {
      const value = await listIllustrationSessions(signal);
      if (!signal?.aborted) { setHistory(value.items); setHistoryError(false); }
    } catch { if (!signal?.aborted) setHistoryError(true); }
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    void Promise.resolve().then(() => { if (!ctl.signal.aborted) void refreshHistory(ctl.signal); });
    return () => ctl.abort();
  }, [refreshHistory, refresh]);

  useEffect(() => {
    const ctl = new AbortController();
    const token = ++generation.current;
    operation.current?.abort();
    void Promise.resolve().then(async () => {
      if (ctl.signal.aborted) return;
      const changed = loadedPath.current !== sessionId;
      loadedPath.current = sessionId;
      setJob(null); setActiveJobId(""); setError(""); setSubmitting(false); setObservationStopped(null);
      if (changed) {
        setSession(null); setMaterials([]); setDraft(""); setViewRevision(0); setRevisionPage(0);
        setSourceRevision(null); setMode(illustrationMode(preferredMode)); pendingSubmission.current = null;
      }
      if (!sessionId || DEMO_MODE) { setLoading(false); return; }
      setLoading(true);
      try {
        const value = await getIllustrationSession(sessionId, ctl.signal);
        if (ctl.signal.aborted || generation.current !== token) return;
        setSession(value); setViewRevision(value.revision);
        const last = value.turns.at(-1);
        if (last && changed) setMode(last.mode);
        setActiveJobId(value.active_job_id ?? "");
        if (last?.status === "failed") {
          const result = await getToolIllustrationJob(last.job_id, ctl.signal);
          if (!ctl.signal.aborted && generation.current === token) setJob(result);
        }
      } catch { if (!ctl.signal.aborted && generation.current === token) setError(tr("loadFailed")); }
      finally { if (!ctl.signal.aborted && generation.current === token) setLoading(false); }
    });
    return () => ctl.abort();
    // Preference changes should not reset an open conversation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, refresh]);

  useEffect(() => {
    if (!activeJobId || !session?.session_id || DEMO_MODE) return;
    const ctl = new AbortController();
    const token = generation.current;
    const startedAt = Date.now();
    let consecutiveFailures = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const valid = () => !ctl.signal.aborted && generation.current === token;
    async function observe() {
      try {
        const value = await getToolIllustrationJob(activeJobId, ctl.signal);
        if (!valid()) return;
        setJob(value); setError(""); consecutiveFailures = 0;
        if (value.status === "queued" || value.status === "running") {
          if (Date.now() - startedAt >= 150_000) {
            setActiveJobId(""); setObservationStopped({ jobId: activeJobId, retryable: true });
          } else timer = setTimeout(() => void observe(), 1200);
        } else {
          const next = await getIllustrationSession(value.session_id, ctl.signal);
          if (!valid()) return;
          setSession(next); setActiveJobId("");
          if (value.status === "ready") { setViewRevision(next.revision); setSourceRevision(null); }
          // The observation controller is torn down as soon as the active job
          // clears. Refresh the rail with its own request so the summary does
          // not remain stuck at “working” after a ready result.
          void refreshHistory();
        }
      } catch (exc) {
        if (valid()) {
          setError(tr("requestFailed"));
          consecutiveFailures++;
          const terminal = exc instanceof IllustrationToolError && ([401, 403, 404].includes(exc.status) || /not_found|not_visible|not_authenticated/.test(exc.code));
          if (terminal || consecutiveFailures >= 3 || Date.now() - startedAt >= 150_000) {
            setActiveJobId(""); setObservationStopped({ jobId: activeJobId, retryable: !terminal });
          } else timer = setTimeout(() => void observe(), 2500);
        }
      }
    }
    void observe();
    return () => { ctl.abort(); clearTimeout(timer); };
  }, [activeJobId, session?.session_id, refreshHistory, tr]);

  useEffect(() => () => { generation.current++; operation.current?.abort(); }, []);
  useEffect(() => { messagesEnd.current?.scrollIntoView({ block: "nearest" }); }, [session?.turns.length, job?.status]);

  const busy = submitting || !!activeJobId || !!observationStopped;
  const revision = session?.revisions.find(row => row.revision === viewRevision) ?? session?.revisions.at(-1);
  const visibleHistoryPage = Math.min(historyPage, pageCount(history.length) - 1);
  const revisions = [...(session?.revisions ?? [])].reverse();
  const visibleRevisionPage = Math.min(revisionPage, pageCount(revisions.length) - 1);
  const stageText = (value: string) => tr(["preparing", "retrieving", "composing", "rendering", "reviewing"].includes(value) ? value : "working");

  function requestError(exc: unknown) {
    if (exc instanceof IllustrationToolError && exc.code === "illustration_session_busy") return tr("busy");
    if (exc instanceof IllustrationToolError && exc.code === "illustration_material_missing") return tr("materialMissing");
    if (exc instanceof IllustrationToolError && exc.code === "illustration_material_version_conflict") return tr("materialConflict");
    if (exc instanceof IllustrationToolError && exc.code === "illustration_source_revision_missing") return tr("sourceMissing");
    if (exc instanceof IllustrationToolError && exc.code === "illustration_disabled") return tr("disabled");
    if (exc instanceof IllustrationToolError && exc.code === "illustration_revision_conflict") {
      pendingSubmission.current = null;
      setRefresh(value => value + 1); return tr("conflict");
    }
    return tr("requestFailed");
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || busy || loading) return;
    const submittedMaterials = mode === "v1" ? [] : materials.map(({ asset_id, version }) => ({ asset_id, version }));
    const fingerprint = JSON.stringify({ message, mode, selected_materials: submittedMaterials, source_revision: sourceRevision });
    if (pendingSubmission.current?.fingerprint !== fingerprint) {
      pendingSubmission.current = { fingerprint, requestId: crypto.randomUUID(), sessionId: session?.session_id ?? null,
        baseRevision: session?.revision ?? 0, sourceRevision: sourceRevision ?? session?.revision ?? 0 };
    }
    const submission = pendingSubmission.current;
    const ctl = new AbortController(); operation.current?.abort(); operation.current = ctl;
    const token = generation.current;
    setSubmitting(true); setError("");
    try {
      const current = session ?? (submission.sessionId ? await getIllustrationSession(submission.sessionId, ctl.signal) : await createIllustrationSession(message.slice(0, 60), ctl.signal));
      submission.sessionId = current.session_id;
      if (!ctl.signal.aborted && generation.current === token) setSession(current);
      const value = await startIllustrationTurn(current.session_id, {
        message, mode, selected_materials: submittedMaterials, source_revision: submission.sourceRevision,
        base_revision: submission.baseRevision, request_id: submission.requestId,
      }, ctl.signal);
      const updated = await getIllustrationSession(current.session_id, ctl.signal);
      if (ctl.signal.aborted || generation.current !== token) return;
      setSession(updated); setJob(value); setActiveJobId(value.status === "queued" || value.status === "running" ? value.job_id : ""); setDraft(""); pendingSubmission.current = null;
      if (value.status === "ready") setViewRevision(updated.revision);
      void refreshHistory(ctl.signal);
      if (!sessionId) router.replace(`/tools/illustration?session=${encodeURIComponent(current.session_id)}`);
    } catch (exc) {
      if (ctl.signal.aborted || generation.current !== token) return;
      setError(requestError(exc));
      if (submission.sessionId) {
        try {
          const recovered = await getIllustrationSession(submission.sessionId, ctl.signal);
          if (ctl.signal.aborted || generation.current !== token) return;
          setSession(recovered);
          const accepted = recovered.turns.find(row => row.request_id === submission.requestId);
          if (accepted) {
            setDraft(""); setError(""); pendingSubmission.current = null;
            setActiveJobId(recovered.active_job_id ?? "");
            setViewRevision(recovered.revision);
            const recoveredJob = await getToolIllustrationJob(accepted.job_id, ctl.signal);
            if (!ctl.signal.aborted && generation.current === token) setJob(recoveredJob);
            if (!sessionId) router.replace(`/tools/illustration?session=${encodeURIComponent(recovered.session_id)}`);
          }
        } catch { /* Keep the logical request ID so the same submission can be retried. */ }
      }
    }
    finally { if (!ctl.signal.aborted && generation.current === token) setSubmitting(false); }
  }
  async function retry(jobId: string) {
    if (busy) return;
    const ctl = new AbortController(); operation.current?.abort(); operation.current = ctl;
    const token = generation.current;
    setSubmitting(true); setError("");
    try {
      const value = await retryToolIllustrationJob(jobId, ctl.signal);
      const updated = await getIllustrationSession(value.session_id, ctl.signal);
      if (ctl.signal.aborted || generation.current !== token) return;
      setJob(value); setSession(updated); setActiveJobId(value.status === "queued" || value.status === "running" ? value.job_id : "");
    } catch (exc) { if (!ctl.signal.aborted && generation.current === token) setError(requestError(exc)); }
    finally { if (!ctl.signal.aborted && generation.current === token) setSubmitting(false); }
  }
  async function removeSession() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true); setError("");
    try {
      await deleteIllustrationSession(deleting.session_id);
      await refreshHistory();
      if (deleting.session_id === sessionId) router.replace("/tools/illustration");
      setDeleting(null);
    } catch { setError(tr("requestFailed")); }
    finally { setDeleteBusy(false); }
  }
  function download() {
    if (!revision?.illustration) return;
    const url = URL.createObjectURL(new Blob([revision.illustration.svg], { type: "image/svg+xml;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `illustration-v${revision.revision}.svg`;
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return <div className="flex h-full min-w-0 flex-col" data-testid="illustration-workspace">
    <header className="flex shrink-0 items-center justify-between gap-4 border-b border-border-light bg-surface px-6 py-4">
      <div><Link href="/tools" className="mb-1 inline-flex items-center gap-1 text-xs text-muted hover:text-accent"><ArrowLeft size={12} aria-hidden="true" />{tr("back")}</Link><h1 className="font-serif text-xl font-semibold text-fg">{tr("illustration")}</h1></div>
      <span className="text-xs text-muted">V1 · V2 · V3</span>
    </header>
    <div className="grid min-h-0 min-w-0 flex-1 grid-cols-[190px_minmax(0,1fr)_minmax(220px,29%)]">
      <aside className="flex min-h-0 flex-col border-r border-border-light bg-surface px-3 py-4" aria-label={tr("sessions")}>
        <Button icon={<Plus size={14} aria-hidden="true" />} disabled={submitting} onClick={() => { router.replace("/tools/illustration"); if (!sessionId) { setDraft(""); setMaterials([]); } }} data-testid="new-illustration-session">{tr("newSession")}</Button>
        <h2 className="mb-3 mt-6 text-[11px] font-medium tracking-wide text-muted">{tr("sessions")}</h2>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {historyError ? <div role="alert" className="text-xs leading-5 text-muted"><p>{tr("loadFailed")}</p><Button className="mt-2" size="sm" variant="outline" onClick={() => setRefresh(v => v + 1)}>{tr("retry")}</Button></div>
            : !history.length ? <p className="px-1 text-xs leading-5 text-muted">{tr("noSessions")}</p>
            : paged(history, visibleHistoryPage).map(row => <div key={row.session_id} className={`mb-2 rounded-lg border ${row.session_id === sessionId ? "border-accent/30 bg-accent-soft" : "border-transparent hover:bg-surface-hover"}`}>
              <div className="flex items-start gap-1"><button type="button" disabled={submitting} onClick={() => router.replace(`/tools/illustration?session=${encodeURIComponent(row.session_id)}`)} data-testid="illustration-session" data-session-id={row.session_id} aria-current={row.session_id === sessionId ? "page" : undefined} className="min-w-0 flex-1 cursor-pointer rounded-lg px-2 py-3 text-left focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">
                <span className="block truncate text-xs font-medium text-fg">{row.title || tr("unnamed")}</span><span className="mt-1 block text-[10px] text-muted">{row.active_job_id ? tr("working") : tr("revision").replace("%n", String(row.revision))}</span></button>
                <Button variant="ghost" size="sm" className="mt-2 shrink-0 px-1.5" disabled={submitting} icon={<Trash2 size={12} aria-hidden="true" />} aria-label={`${tr("delete")}：${row.title}`} onClick={() => setDeleting(row)} />
              </div></div>)}
        </div>
        <Pager page={visibleHistoryPage} total={history.length} onPage={setHistoryPage} />
      </aside>
      <section className="flex min-h-0 min-w-0 flex-col" aria-label={tr("workspace")}>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5" data-testid="illustration-messages" aria-live="polite">
          {loading ? <p role="status" className="py-12 text-center text-sm text-muted">{tr("loading")}</p>
            : !session?.turns.length ? <div className="mx-auto max-w-lg py-10"><Sparkles size={28} className="mb-4 text-accent" aria-hidden="true" /><h2 className="text-xl font-medium text-fg">{tr("startTitle")}</h2><p className="mt-3 text-sm leading-7 text-muted">{DEMO_MODE ? tr("demo") : tr("startDesc")}</p>
              <Button variant="outline" size="sm" className="mt-5" disabled={DEMO_MODE} onClick={() => setDraft(tr("example"))}>{tr("useExample")}</Button></div>
            : session.turns.map(turn => {
              const result = session.revisions.find(row => row.revision === turn.revision);
              const observed = job?.job_id === turn.job_id ? job : null;
              const pending = observed ? observed.status === "queued" || observed.status === "running" : turn.status === "queued" || turn.status === "running";
              return <div key={turn.turn_id} className="mb-6 space-y-3" data-testid="illustration-turn">
                <div className="ml-5 rounded-xl border border-border-light bg-surface px-4 py-3"><p className="mb-2 text-[10px] text-muted">{tr("user")} · {turn.mode.toUpperCase()} · {turn.mode !== "v1" && (turn.selected_materials.length ? tr("manual") : tr("automatic"))}</p><p className="whitespace-pre-wrap break-words text-sm leading-6 text-fg">{turn.message}</p></div>
                <div className="mr-3 rounded-xl border border-border-light bg-surface px-4 py-3"><p className="mb-2 text-[10px] font-medium text-accent-strong">{tr("assistant")}</p>
                  {pending ? <div role="status" className="flex items-center gap-2 text-xs text-muted" data-testid="tool-illustration-generating"><LoaderCircle size={14} className="animate-spin" aria-hidden="true" /><span>{stageText(observed?.stage ?? "preparing")} · {observed?.progress ?? 0}%</span></div>
                    : result ? <><p className="text-xs leading-5 text-fg-secondary">{tr("finished").replace("%n", String(result.revision))}</p><Button className="mt-2" variant="outline" size="sm" onClick={() => setViewRevision(result.revision)}>{tr("viewRevision")} {result.revision}</Button></>
                    : <div role="status" data-testid="tool-illustration-failed"><p className="text-xs leading-6 text-muted">{observed?.failure ? illustrationFailure(observed.failure.code, lang === "en") : tr("failed")}</p>{observed?.failure?.retryable && <Button className="mt-2" demoWrite size="sm" variant="outline" disabled={busy} onClick={() => void retry(turn.job_id)}>{tr("retry")}</Button>}</div>}
                </div>
              </div>;
            })}
          <div ref={messagesEnd} />
        </div>
        <form className="shrink-0 space-y-3 border-t border-border-light bg-surface px-5 py-4" onSubmit={event => { if (DEMO_MODE) { event.preventDefault(); demoReadOnly(); } else void send(event); }}>
          <div className="flex items-end gap-3"><Field className="min-w-0 flex-1" label={tr("mode")}><select className={FIELD_CLS} aria-label={tr("mode")} value={mode} disabled={busy} onChange={e => setMode(illustrationMode(e.target.value))} data-testid="illustration-tool-mode">
            {(["v1", "v2", "v3"] as const).map(value => <option key={value} value={value}>{value.toUpperCase()} · {tr(value)}</option>)}</select></Field>
            {mode !== "v1" && <Button variant="outline" size="sm" disabled={busy || DEMO_MODE} icon={<ImagePlus size={14} aria-hidden="true" />} onClick={() => setPickerOpen(true)} type="button" data-testid="choose-illustration-materials">{tr("materials")}</Button>}
          </div>
          <p className="text-[11px] leading-5 text-muted">{tr(`${mode}Desc`)}</p>
          {mode !== "v1" && (materials.length ? <div className="flex flex-wrap gap-1.5" aria-label={tr("selectedMaterials")}>{materials.map(row => <span key={row.asset_id} className="inline-flex max-w-full items-center gap-1 rounded-full bg-accent-soft px-2 py-1 text-[10px] text-accent-strong"><span className="truncate">{row.title}</span><button type="button" disabled={busy} onClick={() => setMaterials(items => items.filter(item => item.asset_id !== row.asset_id))} aria-label={`${tr("removeMaterial")}：${row.title}`} className="rounded p-0.5 hover:bg-surface"><X size={11} aria-hidden="true" /></button></span>)}<Button size="sm" variant="ghost" type="button" disabled={busy} onClick={() => setMaterials([])}>{tr("clearMaterials")}</Button></div>
            : <p className="text-[11px] leading-5 text-muted" data-testid="automatic-material-search">{tr("autoMaterials")}</p>)}
          <Textarea rows={3} maxLength={2400} aria-label={tr("message")} placeholder={tr("placeholder")} value={draft} disabled={busy || loading} onChange={e => setDraft(e.target.value)} data-testid="illustration-composer"
            onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
          {error && <p role="alert" className="text-xs leading-5 text-danger">{error}{!busy && !draft && <Button className="ml-2" type="button" size="sm" variant="ghost" onClick={() => setRefresh(v => v + 1)}>{tr("retry")}</Button>}</p>}
          {observationStopped && <div role="status" className="text-xs leading-5 text-muted"><p>{tr("observeStopped")}</p><Button className="mt-2" type="button" variant="outline" size="sm" onClick={() => { if (observationStopped.retryable) { setActiveJobId(observationStopped.jobId); setObservationStopped(null); } else setRefresh(v => v + 1); }}>{observationStopped.retryable ? tr("resume") : tr("retry")}</Button></div>}
          <div className="flex items-center justify-between gap-3"><span className="text-[10px] text-muted">{(sourceRevision ?? session?.revision ?? 0) ? tr("basedOn").replace("%n", String(sourceRevision ?? session?.revision)) : tr("firstVersion")}</span>
            <Button type="submit" demoWrite disabled={!draft.trim() || busy || loading} icon={busy ? <LoaderCircle size={14} className="animate-spin" aria-hidden="true" /> : <Send size={14} aria-hidden="true" />} data-testid="send-illustration-request">{submitting ? tr("sending") : activeJobId ? tr("working") : session?.revision ? tr("improve") : tr("send")}</Button></div>
        </form>
      </section>
      <aside className="min-h-0 min-w-0 overflow-y-auto border-l border-border-light bg-surface px-4 py-5" aria-label={tr("preview")} data-testid="illustration-result">
        <h2 className="mb-4 text-sm font-medium text-fg">{tr("preview")}</h2>
        {revision ? <><div className="mb-2 flex items-center justify-between gap-2"><span className="text-xs text-muted">{tr("revision").replace("%n", String(revision.revision))} · {revision.mode.toUpperCase()}</span>{revision.revision === session?.revision && <span className="text-[10px] text-accent-strong">{tr("latest")}</span>}</div>{revision.illustration ? <QuestionIllustration illustration={revision.illustration as import("@/lib/types").QuestionIllustrationData} /> : null}
          <Button variant="outline" size="sm" icon={<Download size={13} aria-hidden="true" />} onClick={download}>{tr("download")}</Button>
          <Button variant="outline" size="sm" className="mt-2" disabled={busy} onClick={() => setSourceRevision(revision.revision)} data-testid="use-illustration-revision">{revision.revision === (sourceRevision ?? session?.revision) ? tr("usingAsBase") : tr("useAsBase")}</Button>
          <div className="mt-6 border-t border-border-light pt-4"><h3 className="mb-2 text-xs font-medium text-fg">{tr("revisions")}</h3><p className="mb-3 text-[10px] leading-5 text-muted">{tr("chooseBase")}</p><div className="space-y-1">{paged(revisions, visibleRevisionPage).map(row => <button key={row.revision} type="button" className={`flex w-full cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-xs ${row.revision === revision.revision ? "bg-accent-soft text-accent-strong" : "text-muted hover:bg-surface-hover"}`} onClick={() => setViewRevision(row.revision)} data-testid="illustration-revision" data-revision={row.revision} aria-pressed={row.revision === revision.revision}><span>{tr("revision").replace("%n", String(row.revision))}</span><span>{row.mode.toUpperCase()}</span></button>)}</div><Pager page={visibleRevisionPage} total={revisions.length} onPage={setRevisionPage} /></div></>
          : <div className="rounded-xl border border-dashed border-border px-4 py-14 text-center"><ImagePlus size={28} className="mx-auto mb-3 text-muted/60" aria-hidden="true" /><p className="text-xs leading-6 text-muted">{tr("noPreview")}</p></div>}
      </aside>
    </div>
    {pickerOpen && <MaterialPicker selected={materials} onClose={() => setPickerOpen(false)} onApply={items => { setMaterials(items); setPickerOpen(false); }} />}
    {deleting && <ConfirmModal open onClose={() => { if (!deleteBusy) setDeleting(null); }} onConfirm={() => void removeSession()} title={tr("deleteTitle")} desc={tr("deleteDesc")} confirmText={tr("delete")} cancelText={tr("cancel")} />}
  </div>;
}
