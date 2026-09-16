"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ImageIcon, LoaderCircle, Play } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { FIELD_CLS, Input, LABEL_CLS } from "@/components/ui/Input";
import { getEvalConcepts, getEvalWorkspaces, getUserProfile, updateUserProfile } from "@/lib/api-modules";
import { useAuthStore } from "@/lib/auth-store";
import type { ConceptEvaluationView, WorkspaceEvaluationListItem } from "@/lib/types-modules";
import type { PageTr } from "./common";

export interface AssessmentStartIntent {
  workspaceId: string;
  conceptKeys: string[];
  purpose: "adaptive" | "diagnose" | "practice";
  count: number;
  illustrationRequest: "auto" | "required";
}

type ConfigProps = {
  tr: PageTr;
  lang: "zh" | "en";
  busy: boolean;
  onStart: (intent: AssessmentStartIntent) => void;
};
type LoadState = "loading" | "ready" | "error";
const MAX_CONCEPTS = 8;

export function ConfigCard(props: ConfigProps) {
  const userId = useAuthStore((store) => store.user?.id);
  return <Configuration key={userId ?? "guest"} {...props} userId={userId} />;
}

function Configuration({ tr, lang, busy, onStart, userId }: ConfigProps & { userId?: string }) {
  const text = (zh: string, en: string) => lang === "en" ? en : zh;
  const id = useId();
  const mounted = useRef(false);
  const savingLock = useRef(false);
  const [workspaces, setWorkspaces] = useState<WorkspaceEvaluationListItem[]>([]);
  const [workspaceState, setWorkspaceState] = useState<LoadState>("loading");
  const [workspaceRetry, setWorkspaceRetry] = useState(0);
  const [workspaceId, setWorkspaceId] = useState("");
  const [concepts, setConcepts] = useState<ConceptEvaluationView[]>([]);
  const [conceptLoadState, setConceptLoadState] = useState<LoadState>("loading");
  const [conceptRetry, setConceptRetry] = useState(0);
  const [picked, setPicked] = useState<string[]>([]);
  const [purpose, setPurpose] = useState<AssessmentStartIntent["purpose"]>("adaptive");
  const [count, setCount] = useState("1");
  const [profileState, setProfileState] = useState<LoadState>(userId ? "loading" : "ready");
  const [profileRetry, setProfileRetry] = useState(0);
  const [available, setAvailable] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [required, setRequired] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    getEvalWorkspaces(0, 100).then((response) => {
      if (!active) return;
      const items = response.items ?? [];
      setWorkspaces(items);
      setWorkspaceId(items[0]?.workspace_id ?? "");
      setWorkspaceState("ready");
    }).catch(() => { if (active) setWorkspaceState("error"); });
    return () => { active = false; };
  }, [workspaceRetry]);

  useEffect(() => {
    if (!workspaceId) return;
    let active = true;
    getEvalConcepts(workspaceId, { limit: 100 }).then((response) => {
      if (!active) return;
      setConcepts(response.items ?? []);
      setConceptLoadState("ready");
    }).catch(() => { if (active) setConceptLoadState("error"); });
    return () => { active = false; };
  }, [workspaceId, conceptRetry]);

  useEffect(() => {
    if (!userId) return;
    let active = true;
    getUserProfile().then((response) => {
      if (!active) return;
      setAvailable(response.quiz_svg_available === true);
      setEnabled(response.profile.prefs?.quiz_svg_enabled !== false);
      setRequired(false);
      setProfileState("ready");
    }).catch(() => { if (active) setProfileState("error"); });
    return () => { active = false; };
  }, [userId, profileRetry]);

  const ready = Boolean(userId && profileState === "ready" && available && enabled);
  const parsedCount = Number(count);
  const countValid = count.trim() !== "" && Number.isInteger(parsedCount) && parsedCount >= 1 && parsedCount <= 20;
  const profileBlocked = Boolean(userId && (profileState !== "ready" || saveError));
  const canStart = !busy && !saving && !profileBlocked && workspaceState === "ready"
    && conceptLoadState === "ready" && Boolean(workspaceId) && picked.length > 0 && countValid;

  async function toggleIllustration() {
    if (!userId || busy || saveError || profileState !== "ready" || !available || savingLock.current) return;
    savingLock.current = true;
    setSaving(true);
    setSaveError(false);
    try {
      const profile = await updateUserProfile({ prefs: { quiz_svg_enabled: !enabled } });
      const user = useAuthStore.getState().user;
      if (!mounted.current || user?.id !== userId) return;
      const nextEnabled = profile.prefs?.quiz_svg_enabled !== false;
      setEnabled(nextEnabled);
      if (!nextEnabled) setRequired(false);
      useAuthStore.setState({ user: { ...user, profile } });
    } catch {
      if (mounted.current) setSaveError(true);
    } finally {
      savingLock.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  const status = !userId ? tr("illustration.login")
    : profileState === "loading" ? text("正在读取插图设置…", "Loading diagram settings…")
    : saveError ? tr("illustration.saveFailed")
    : profileState === "error" ? tr("illustration.loadFailed")
    : !available ? tr("illustration.unavailableSetting")
    : saving ? tr("illustration.saving")
    : enabled ? text("已开启 · AI 按题意决定是否配图", "On · AI decides whether a diagram is useful")
    : text("已关闭 · 不生成新图，历史题图保留", "Off · existing diagrams remain available");
  const sortedConcepts = [...concepts].sort((first, second) => {
    const rank = (concept: ConceptEvaluationView) => !concept.state || concept.state === "not_observed" ? 1 : 0;
    return rank(first) - rank(second);
  });

  return (
    <Card pad={false} className="min-w-0 overflow-hidden">
      <form className="space-y-4 p-4 sm:p-5" onSubmit={(event) => {
        event.preventDefault();
        if (canStart && !savingLock.current) onStart({
          workspaceId, conceptKeys: picked, purpose, count: parsedCount,
          illustrationRequest: ready && required ? "required" : "auto",
        });
      }}>
        <header className="flex items-start gap-2.5">
          <Play size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-accent" />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-fg">{tr("config.title")}</h2>
            <p className="mt-1 text-xs leading-5 text-muted">{tr("config.desc")}</p>
          </div>
        </header>

        {workspaceState === "loading" ? (
          <p role="status" className="text-sm text-muted">{text("正在读取学习区…", "Loading workspaces…")}</p>
        ) : workspaceState === "error" ? (
          <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-danger">
            {text("读取学习区失败。", "Could not load workspaces.")}
            <Button type="button" variant="outline" size="sm" onClick={() => {
              setWorkspaceState("loading");
              setWorkspaceRetry((value) => value + 1);
            }}>{tr("illustration.retry")}</Button>
          </div>
        ) : workspaces.length === 0 ? (
          <p className="rounded-lg bg-surface-sunken p-3 text-sm leading-6 text-muted">
            {text("请先创建学习区并选择教材，然后回来开始测评。", "Create a workspace and select textbooks before starting an assessment.")}
          </p>
        ) : (
          <fieldset disabled={busy || saving} className="min-w-0 space-y-4">
            <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_6rem]">
              <label className="min-w-0">
                <span className={LABEL_CLS}>{text("学习区", "Workspace")}</span>
                <select className={FIELD_CLS} value={workspaceId} onChange={(event) => {
                  setWorkspaceId(event.target.value);
                  setPicked([]);
                  setConcepts([]);
                  setConceptLoadState("loading");
                }}>
                  {workspaces.map((workspace) => <option key={workspace.workspace_id} value={workspace.workspace_id}>
                    {workspace.workspace_name || workspace.workspace_id}
                  </option>)}
                </select>
              </label>
              <label className="min-w-0">
                <span className={LABEL_CLS}>{text("目的", "Purpose")}</span>
                <select className={FIELD_CLS} value={purpose} onChange={(event) => setPurpose(event.target.value as AssessmentStartIntent["purpose"])}>
                  <option value="adaptive">{text("自适应摸底", "Adaptive")}</option>
                  <option value="diagnose">{text("针对验证", "Diagnose")}</option>
                  <option value="practice">{text("练习", "Practice")}</option>
                </select>
              </label>
              <label className="min-w-0">
                <span className={LABEL_CLS}>{text("题量", "Count")}</span>
                <input type="number" className={FIELD_CLS} min={1} max={20} step={1} required value={count}
                  aria-invalid={!countValid} onChange={(event) => setCount(event.target.value)} />
              </label>
            </div>
            <section aria-labelledby={`${id}-concepts`}>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                <h3 id={`${id}-concepts`} className="font-medium text-fg-secondary">{text("诊断概念", "Concepts")}</h3>
                <span className="text-muted">{text("已选", "Selected")} {picked.length}/{MAX_CONCEPTS}</span>
              </div>
              <div className="flex max-h-40 min-w-0 flex-wrap content-start items-start gap-2 overflow-y-auto rounded-lg border border-border-light p-2.5">
                {conceptLoadState === "loading" ? <p role="status" className="text-xs text-muted">{text("正在读取概念…", "Loading concepts…")}</p>
                  : conceptLoadState === "error" ? <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-danger">
                    {text("读取概念失败。", "Could not load concepts.")}
                    <Button type="button" variant="outline" size="sm" onClick={() => {
                      setConceptLoadState("loading");
                      setConceptRetry((value) => value + 1);
                    }}>{tr("illustration.retry")}</Button>
                  </div>
                  : sortedConcepts.length === 0 ? <p className="text-xs text-muted">{text("该学习区暂无教材概念。", "No textbook concepts in this workspace.")}</p>
                  : sortedConcepts.map((concept) => {
                    const key = concept.concept_ref.key || concept.concept_ref.concept_id;
                    const selected = picked.includes(key);
                    return <button key={key} type="button" aria-pressed={selected}
                      disabled={!selected && picked.length >= MAX_CONCEPTS}
                      onClick={() => setPicked((previous) => previous.includes(key)
                        ? previous.filter((item) => item !== key)
                        : previous.length < MAX_CONCEPTS ? [...previous, key] : previous)}
                      className={`max-w-full cursor-pointer break-words rounded-lg border px-2.5 py-1.5 text-left text-xs leading-5 disabled:cursor-not-allowed disabled:opacity-50 ${selected
                        ? "border-accent bg-accent-soft text-accent-strong"
                        : "border-border text-fg-secondary hover:border-accent"}`}>
                      {concept.concept_ref.display_name || concept.concept_ref.concept_id}
                    </button>;
                  })}
              </div>
            </section>
          </fieldset>
        )}

        <section data-testid="assessment-illustration-options" aria-labelledby={`${id}-illustration`}
          className="rounded-lg border border-border-light bg-surface-sunken p-3">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3">
            <div className="flex min-w-0 items-start gap-2">
              <ImageIcon size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-accent" />
              <div className="min-w-0">
                <h3 id={`${id}-illustration`} className="text-sm font-medium text-fg">{tr("illustration.optionsTitle", tr("illustration.title"))}</h3>
                <p id={`${id}-status`} role="status" className="mt-1 break-words text-xs leading-5 text-muted">{status}</p>
              </div>
            </div>
            <Button type="button" size="sm" variant={ready ? "primary" : "outline"}
              className="shrink-0 whitespace-nowrap"
              aria-label={ready ? text("已开启", "On") : text("已关闭", "Off")}
              aria-pressed={ready} aria-describedby={`${id}-status`}
              disabled={!userId || profileState !== "ready" || !available || saving || busy || saveError}
              onClick={() => void toggleIllustration()}>
              {saving ? <LoaderCircle size={14} aria-hidden="true" className="animate-spin" /> : ready ? text("开启", "On") : text("关闭", "Off")}
            </Button>
          </div>
          <p className="mt-2 text-xs leading-5 text-muted">{tr("illustration.desc")}</p>
          <label className="mt-3 grid cursor-pointer grid-cols-[1rem_minmax(0,1fr)] items-start gap-2">
            <Input type="checkbox" className="mt-0.5" checked={ready && required}
              disabled={!ready || saving || busy || saveError} aria-describedby={`${id}-required-help`}
              onChange={(event) => setRequired(event.target.checked)} />
            <span className="min-w-0 text-xs leading-5">
              <span className="font-medium text-fg">{tr("illustration.required")}</span>
              <span id={`${id}-required-help`} className="mt-0.5 block text-muted">{text("仅本次每题必须配图；不勾选则由 AI 自动判断。", "Require a diagram for every question this time; otherwise AI decides.")}</span>
            </span>
          </label>
          {(profileState === "error" || saveError) && <div role="alert" className="mt-2 flex flex-wrap items-center gap-2 text-xs text-danger">
            <span>{tr(saveError ? "illustration.saveFailed" : "illustration.loadFailed")}</span>
            <Button type="button" variant="outline" size="sm" disabled={busy || saving} onClick={() => {
              setSaveError(false);
              setProfileState("loading");
              setProfileRetry((value) => value + 1);
            }}>{tr("illustration.retry")}</Button>
          </div>}
        </section>

        <footer className="flex flex-col items-stretch gap-3 border-t border-border-light pt-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="min-w-0 text-xs leading-5 text-muted">{text("请选择 1–8 个教材概念，题量为 1–20。", "Select 1–8 textbook concepts and 1–20 questions.")}</p>
          <Button type="submit" size="lg" className="shrink-0 whitespace-nowrap" disabled={!canStart}
            icon={busy ? <LoaderCircle size={15} aria-hidden="true" className="animate-spin" /> : <Play size={15} aria-hidden="true" />}>
            {busy ? tr("config.starting") : tr("config.start")}
          </Button>
        </footer>
      </form>
    </Card>
  );
}
