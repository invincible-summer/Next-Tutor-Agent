"use client";

import { useEffect, useState } from "react";
import { Check, Cpu, ScanLine } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { t, LANGS, GRADE_LABELS } from "@/lib/i18n";
import { makePageT } from "@/lib/i18n-page";
import { getModelInfo } from "@/lib/api";
import { updateUserProfile } from "@/lib/api-modules";
import { useAuthStore } from "@/lib/auth-store";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Hint } from "@/components/ui/Hint";
import { useToast } from "@/components/ui/Toast";
import { STRINGS } from "@/app/(workspace)/settings/strings";
import { gradeForApi, type Grade } from "@/lib/types";

export function LocalSettings({ section, onDirtyChange }: { section: "general" | "learning" | "about"; onDirtyChange?: (dirty: boolean) => void }) {
  const { lang, setLang, themePreference, setTheme, defaultGrade, setDefaultGrade, outputLanguage, setOutputLanguage, fontScale, setFontScale } = useUIStore();
  const user = useAuthStore((s) => s.user);
  const notify = useToast();
  const [gradeStatus, setGradeStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const [modelInfo, setModelInfo] = useState<Awaited<ReturnType<typeof getModelInfo>> | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const tr = (key: string) => t(lang, key);
  const st = makePageT(lang, STRINGS);
  useEffect(() => {
    onDirtyChange?.(gradeStatus === "saving");
    return () => onDirtyChange?.(false);
  }, [gradeStatus, onDirtyChange]);
  const changeGrade = async (value: Grade) => {
    if (gradeStatus === "saving") return;
    if (!user) { setDefaultGrade(value); setGradeStatus("saved"); notify(st("settings.saved")); return; }
    setGradeStatus("saving");
    try {
      const profile = await updateUserProfile({ grade: gradeForApi(value) });
      if (useAuthStore.getState().user?.id === user.id) {
        useAuthStore.setState((state) => state.user?.id === user.id ? { user: { ...state.user, profile } } : {});
        setDefaultGrade(value, false);
        setGradeStatus("saved");
        notify(st("settings.saved"));
      }
    } catch { setGradeStatus("failed"); notify(st("settings.saveFailed"), "error"); }
  };
  useEffect(() => {
    if (section !== "about") return;
    let alive = true;
    getModelInfo().then((value) => { if (alive) { setModelInfo(value); setError(false); } }).catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [section, retry]);
  const help = (label: string, text: string) => <Hint label={st("settings.help").replace("{label}", label)} text={text} align="end" />;
  const updateLocal = (change: () => void) => { change(); notify(st("settings.updated")); };
  function choices(label: string, hint: string, options: { value: string | number; label: string; hint: string }[], selected: string | number, change: (value: string | number) => void, disabled = false) {
    return <div className="py-4"><h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-fg">{label}{help(label, hint)}</h3><div role="group" aria-label={label} className="flex flex-wrap gap-x-3 gap-y-2">{options.map((option) => <span key={option.value} className="inline-flex items-center gap-1.5"><button type="button" disabled={disabled} aria-pressed={option.value === selected} onClick={() => { if (option.value !== selected) change(option.value); }}
      className={`inline-flex min-h-9 items-center gap-1.5 rounded-lg border px-3 text-xs outline-none focus-visible:ring-2 focus-visible:ring-accent ${selected === option.value ? "border-accent/30 bg-accent-soft text-accent-strong" : "border-border text-fg-secondary hover:bg-surface-hover"}`}>
      {option.value === selected && <Check size={12} aria-hidden="true" />}{option.label}
    </button>{help(option.label, option.hint)}</span>)}</div></div>;
  }
  return <Card>
    {section === "general" && <div className="divide-y divide-border-light">
      {choices(tr("settings.language"), st("settings.language.hint"), LANGS.map((l) => ({ value: l.code, label: l.label, hint: st(`settings.language.${l.code}.hint`) })), lang, (v) => {
        const nextLang = v as "zh" | "en"; setLang(nextLang); notify(makePageT(nextLang, STRINGS)("settings.updated"));
      })}
      {choices(tr("settings.theme"), st("settings.theme.hint"), ["system", "light", "dark"].map((value) => ({ value, label: tr(`settings.theme.${value}`), hint: st(`settings.theme.${value}.hint`) })), themePreference, (v) => updateLocal(() => setTheme(v as "system" | "light" | "dark")))}
      {choices(tr("settings.font"), st("settings.font.hint"), [1, 1.25, 1.5, 1.75].map((value, i) => ({ value, label: tr(["settings.font.sm", "settings.font.md", "settings.font.lg", "settings.font.xl"][i]), hint: st(`settings.font.${["sm", "md", "lg", "xl"][i]}.hint`) })), fontScale, (v) => updateLocal(() => setFontScale(Number(v))))}
      <p className="pt-4 text-xs leading-relaxed text-muted">{st("settings.local")}</p>
    </div>}
    {section === "learning" && <div className="divide-y divide-border-light">
      {choices(tr("settings.answer.lang"), st("settings.answer.hint"), ["auto", "zh", "en"].map((value) => ({ value, label: tr(`settings.answer.${value}`), hint: st(`settings.answer.${value}.hint`) })), outputLanguage, (v) => updateLocal(() => setOutputLanguage(v as "auto" | "zh" | "en")))}
      <div aria-busy={gradeStatus === "saving"}>{choices(tr("settings.grade"), st(user ? "settings.gradeHint" : "settings.guestGradeHint"), GRADE_LABELS[lang].map((g, i) => ({ value: g.token, label: g.label, hint: st(`settings.grade.${["auto", "primary", "middle", "high", "undergrad"][i]}.hint`) })), defaultGrade, (v) => void changeGrade(v as Grade), gradeStatus === "saving")}</div>
      <p className="pt-4 text-xs leading-relaxed text-muted">{st(user ? "settings.gradeHint" : "settings.guestGradeHint")}</p>
    </div>}
    {section === "about" && <>
      <h3 className="flex items-center gap-2 text-sm font-medium text-fg"><Cpu size={16} />{tr("settings.model")}</h3>
      {error ? <div className="mt-4 flex items-center gap-3 text-xs text-danger">{st("settings.loadFailed")}<Button variant="ghost" size="sm" onClick={() => setRetry(retry + 1)}>{st("settings.retry")}</Button>{help(st("settings.retry"), st("settings.retry.hint"))}</div> : !modelInfo ? <p role="status" className="mt-4 text-xs text-muted">{st("settings.loading")}</p> : <dl className="mt-4 divide-y divide-border-light text-sm">
        <div className="flex justify-between gap-4 py-3"><dt className="flex items-center gap-2 text-muted">{tr("settings.model.main")}{help(tr("settings.model.main"), st("settings.model.main.hint"))}</dt><dd className="break-all text-fg-secondary">{modelInfo.llm_model}</dd></div>
        <div className="flex justify-between gap-4 py-3"><dt className="flex items-center gap-2 text-muted"><ScanLine size={13} />{tr("settings.model.vision")}<Hint text={tr("settings.model.vision.tooltip")} /></dt><dd className="text-fg-secondary">{tr(modelInfo.multimodal_configured ? "settings.model.vision.on" : "settings.model.vision.off")}</dd></div>
        {modelInfo.voice_models && <>
          <div className="flex justify-between gap-4 py-3"><dt className="flex shrink-0 items-center gap-2 text-muted">{st("settings.model.localVoice")}{help(st("settings.model.localVoice"), st("settings.model.localVoice.hint"))}</dt><dd className="text-right text-fg-secondary"><span>{modelInfo.voice_models.local.model}</span><p className="mt-1 text-xs text-muted">{st(modelInfo.voice_models.local.enabled ? "settings.model.enabled" : "settings.model.disabled")} · {modelInfo.voice_models.local.voice}</p></dd></div>
          <div className="flex justify-between gap-4 py-3"><dt className="flex shrink-0 items-center gap-2 text-muted">{st("settings.model.cloudVoice")}{help(st("settings.model.cloudVoice"), st("settings.model.cloudVoice.hint"))}</dt><dd className="min-w-0 break-all text-right text-fg-secondary"><span>{modelInfo.voice_models.cloud.model}</span><p className="mt-1 text-xs text-muted">{st(modelInfo.voice_models.cloud.configured ? "settings.model.configured" : "settings.model.unconfigured")}</p><p className="mt-1 text-xs text-muted">{modelInfo.voice_models.cloud.voices.join(" / ")}</p></dd></div>
          <div className="flex justify-between gap-4 py-3"><dt className="flex items-center gap-2 text-muted">{st("settings.model.autoVoice")}{help(st("settings.model.autoVoice"), st("settings.model.autoVoice.hint"))}</dt><dd className="text-fg-secondary">{st("settings.model.localFirst")}</dd></div>
        </>}
      </dl>}
    </>}
  </Card>;
}
