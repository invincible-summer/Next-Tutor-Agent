"use client";
import { useEffect, useRef, useState } from "react";
import { Settings, Sun, Moon, Languages, Check, Cpu, ScanLine } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { t, LANGS, GRADE_LABELS } from "@/lib/i18n";
import { getModelInfo } from "@/lib/api";
import { Hint } from "@/components/ui/Hint";
import type { Grade } from "@/lib/types";

export function SettingsGear() {
  const { lang, setLang, theme, toggleTheme, grade, setGrade, outputLanguage, setOutputLanguage, fontScale, setFontScale } = useUIStore();
  const [open, setOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);
  const [modelInfo, setModelInfo] = useState<{ llm_model: string; multimodal_configured: boolean; multimodal_model: string } | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    if (!open || modelInfo) return;
    let cancelled = false;
    getModelInfo().then((info) => { if (!cancelled) setModelInfo(info); }).catch(() => {});
    return () => { cancelled = true; };
  }, [open, modelInfo]);

  const tr = (k: string, fb?: string) => t(lang, k, fb);

  return (
    <div className="relative" ref={popRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={tr("settings.title")}
        title={tr("settings.title")}
        className="flex items-center justify-center w-8 h-8 rounded-lg text-muted hover:text-fg hover:bg-surface-hover transition-colors"
      >
        <Settings size={18} className={open ? "rotate-90 transition-transform duration-200" : "transition-transform duration-200"} />
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-50 max-h-[calc(100vh-4.5rem)] w-60 overflow-y-auto overscroll-contain rounded-xl border border-border bg-surface shadow-lg shadow-black/5 p-2">
          <p className="px-2 pt-1 pb-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-muted">
            {tr("settings.title")}
          </p>

          {/* Model info */}
          <div className="px-2 py-1.5">
            <div className="flex items-center gap-1.5 text-xs text-muted mb-1.5">
              <Cpu size={13} />
              <span>{tr("settings.model")}</span>
            </div>
            <div className="space-y-1">
              <div className="flex items-center justify-between rounded-md bg-surface-hover/40 px-2 py-1">
                <span className="text-[0.7rem] text-muted">{tr("settings.model.main")}</span>
                <span className="text-[0.7rem] font-medium text-fg-secondary truncate max-w-[120px]">{modelInfo?.llm_model ?? "..."}</span>
              </div>
              <div className="flex items-center justify-between rounded-md bg-surface-hover/40 px-2 py-1">
                <span className="flex items-center gap-1 text-[0.7rem] text-muted">
                  <ScanLine size={11} />
                  {tr("settings.model.vision")}
                </span>
                <span className="flex items-center gap-1">
                  <span className={"text-[0.7rem] font-medium " + (modelInfo?.multimodal_configured ? "text-accent" : "text-muted")}>
                    {modelInfo == null ? "..." : (modelInfo.multimodal_configured ? tr("settings.model.vision.on") : tr("settings.model.vision.off"))}
                  </span>
                  <Hint text={tr("settings.model.vision.tooltip")} iconSize={11} width="w-44" align="end" />
                </span>
              </div>
            </div>
          </div>

          <div className="border-t border-border-light my-1" />

          <div className="px-2 py-1.5">
            <div className="flex items-center gap-1.5 text-xs text-muted mb-1.5">
              <Languages size={13} />
              <span>{tr("settings.language")}</span>
            </div>
            <div className="grid grid-cols-2 gap-1">
              {LANGS.map((l) => (
                <button
                  key={l.code}
                  onClick={() => setLang(l.code)}
                  className={`flex items-center justify-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${
                    lang === l.code
                      ? "bg-accent-soft/40 text-accent"
                      : "text-fg-secondary hover:bg-surface-hover"
                  }`}
                >
                  {lang === l.code && <Check size={12} />}
                  {l.label}
                </button>
              ))}
            </div>
          </div>

          <div className="border-t border-border-light my-1" />

          <div className="px-2 py-1.5">
            <div className="flex items-center gap-1.5 text-xs text-muted mb-1.5">
              <Languages size={13} />
              <span>{tr("settings.answer.lang")}</span>
            </div>
            <div className="grid grid-cols-3 gap-1">
              {([
                { v: "auto" as const, label: tr("settings.answer.auto") },
                { v: "zh" as const, label: tr("settings.answer.zh") },
                { v: "en" as const, label: tr("settings.answer.en") },
              ]).map((o) => (
                <button
                  key={o.v}
                  onClick={() => setOutputLanguage(o.v)}
                  className={`flex items-center justify-center gap-1 rounded-md px-1 py-1.5 text-[0.7rem] transition-colors ${
                    outputLanguage === o.v
                      ? "bg-accent-soft/40 text-accent"
                      : "text-fg-secondary hover:bg-surface-hover"
                  }`}
                >
                  {outputLanguage === o.v && <Check size={11} />}
                  {o.label}
                </button>
              ))}
            </div>
          </div>

          <div className="border-t border-border-light my-1" />

          <div className="px-2 py-1.5">
            <div className="flex items-center gap-1.5 text-xs text-muted mb-1.5">
              <Sun size={13} />
              <span>{tr("settings.theme")}</span>
            </div>
            <div className="grid grid-cols-2 gap-1">
              <button
                onClick={() => { if (theme === "dark") toggleTheme(); }}
                className={`flex items-center justify-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${
                  theme === "light" ? "bg-accent-soft/40 text-accent" : "text-fg-secondary hover:bg-surface-hover"
                }`}
              >
                <Sun size={12} /> {tr("settings.theme.light")}
              </button>
              <button
                onClick={() => { if (theme === "light") toggleTheme(); }}
                className={`flex items-center justify-center gap-1 rounded-md px-2 py-1.5 text-xs transition-colors ${
                  theme === "dark" ? "bg-accent-soft/40 text-accent" : "text-fg-secondary hover:bg-surface-hover"
                }`}
              >
                <Moon size={12} /> {tr("settings.theme.dark")}
              </button>
            </div>
          </div>

          <div className="border-t border-border-light my-1" />

          <div className="px-2 py-1.5">
            <div className="flex items-center gap-1.5 text-xs text-muted mb-1.5">
              <span className="text-xs">{tr("settings.font")}</span>
            </div>
            <div className="grid grid-cols-4 gap-1">
              {([
                { v: 1, key: "settings.font.sm" },
                { v: 1.25, key: "settings.font.md" },
                { v: 1.5, key: "settings.font.lg" },
                { v: 1.75, key: "settings.font.xl" },
              ] as const).map((o) => (
                <button
                  key={o.key}
                  onClick={() => setFontScale(o.v)}
                  className={`flex items-center justify-center rounded-md px-1 py-1.5 text-[0.7rem] transition-colors ${
                    fontScale === o.v
                      ? "bg-accent-soft/40 text-accent"
                      : "text-fg-secondary hover:bg-surface-hover"
                  }`}
                >
                  {fontScale === o.v && <Check size={11} />}
                  {tr(o.key)}
                </button>
              ))}
            </div>
          </div>

          <div className="border-t border-border-light my-1" />

          <div className="px-2 py-1.5">
            <div className="flex items-center gap-1.5 text-xs text-muted mb-1.5">
              <span className="text-xs">{tr("settings.grade")}</span>
            </div>
            <div className="grid grid-cols-5 gap-1">
              {GRADE_LABELS[lang].map((g) => (
                <button
                  key={g.token}
                  onClick={() => setGrade(g.token as Grade)}
                  className={`rounded-md px-1 py-1.5 text-[0.65rem] transition-colors ${
                    grade === g.token
                      ? "bg-accent-soft/40 text-accent"
                      : "text-fg-secondary hover:bg-surface-hover"
                  }`}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
