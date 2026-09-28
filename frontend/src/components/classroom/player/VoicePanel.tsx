"use client";
/* 课堂语音源面板（plan.md §11.1/§14.2，PUT runs/{id}/audio-profile）。
 *
 * 控制条按钮（AudioLines + 当前源徽标 云端/本地/文字）打开 motion-pop：
 * 策略四选（自动/云端/本地/静音，不可用项按 capabilities 禁用并给原因）→
 * 音色选择（随策略过滤；本地 = melo-zh，其余为云端批准音色）→ 试听
 * （useVoicePreview 独立通道，开始前暂停讲授）→ 「应用」走
 * player.applyAudioProfile（CAS；成功后当前段起用新音色重讲）。
 * capabilities 首次打开时懒加载并模块级缓存；对话框每次打开重新挂载，
 * 表单从当前 audio_profile 初始化（无 effect 同步 setState）。
 */
import { useEffect, useState } from "react";
import { AudioLines, Loader2, Play, Cloud, Laptop, VolumeX, Sparkles, X } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import {
  getClassroomCapabilities, type RunPublicExtra,
} from "@/lib/api-classroom";
import type {
  ClassroomCapabilities, LessonLanguage, VoicePolicy,
} from "@/lib/types-classroom.generated";
import { useVoicePreview } from "@/lib/classroom/useVoicePreview";
import { FIELD_CLS } from "@/components/ui/Input";
import { STRINGS } from "../strings";

/** 后端音色约定：本地 MeloTTS 固定 voice_id；audio_profile.provider 为
 * "azure"（云端）/ "melo"（本地）/ ""（静音→文字课堂）。 */
const LOCAL_VOICE_ID = "melo-zh";
const POLICIES: VoicePolicy[] = ["auto", "cloud", "local", "silent"];

interface AudioProfileView {
  policy?: string;
  provider?: string;
  voice_id?: string;
  allow_local_fallback?: boolean;
  language?: string;
}

type ApplyFn = (prefs: { policy: VoicePolicy; voice_id?: string | null;
  allow_local_fallback?: boolean }) => Promise<boolean>;

let capsCache: ClassroomCapabilities | null = null;
let capsInflight: Promise<ClassroomCapabilities | null> | null = null;

/** 能力懒加载（模块级缓存；失败不缓存，下次打开重试）。 */
function loadCapabilities(): Promise<ClassroomCapabilities | null> {
  if (capsCache) return Promise.resolve(capsCache);
  if (!capsInflight) {
    capsInflight = getClassroomCapabilities()
      .then((c) => { capsCache = c; return c; })
      .catch(() => null)
      .finally(() => { capsInflight = null; });
  }
  return capsInflight;
}

function sourceKeyOf(profile: AudioProfileView | undefined): string {
  const provider = String(profile?.provider ?? "");
  return provider === "azure"
    ? "cls.voice.source.cloud"
    : provider.startsWith("melo")
      ? "cls.voice.source.local"
      : "cls.voice.source.text";
}

export function VoicePanel(
  { workspaceId, run, onApply, onPreviewStart, onOpenChange }:
  {
    workspaceId: string;
    run: RunPublicExtra | null;
    onApply: ApplyFn;
    /** 试听/切换前暂停讲授（避免与课堂音频叠声）。 */
    onPreviewStart?: () => void;
    onOpenChange?: (open: boolean) => void;
  },
) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const [open, setOpen] = useState(false);
  const profile = run?.audio_profile as AudioProfileView | undefined;
  const sourceKey = sourceKeyOf(profile);

  useEffect(() => { onOpenChange?.(open); }, [open, onOpenChange]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              aria-label={`${tr("cls.voice.title")}: ${tr(sourceKey)}`}
              className="inline-flex h-9 min-w-11 cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-border bg-bg/60 px-2.5 text-fg-secondary transition-colors hover:border-accent/40 hover:text-accent-strong">
        <AudioLines size={16} />
        <span className="text-[0.62rem] leading-none">{tr(sourceKey)}</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40 cursor-default"
               aria-hidden="true" onClick={() => setOpen(false)} />
          <VoicePanelDialog
            workspaceId={workspaceId} profile={profile} tr={tr} onClose={() => setOpen(false)}
            onPreviewStart={onPreviewStart}
            onApply={async (prefs) => {
              const ok = await onApply(prefs);
              if (ok) setOpen(false);
              return ok;
            }} />
        </>
      )}
    </div>
  );
}

function VoicePanelDialog(
  { workspaceId, profile, onApply, onPreviewStart, onClose, tr }:
  {
    workspaceId: string;
    profile: AudioProfileView | undefined;
    onClose: () => void;
    onApply: ApplyFn;
    onPreviewStart?: () => void;
    tr: (key: string, fallback?: string) => string;
  },
) {
  const [caps, setCaps] = useState<ClassroomCapabilities | null>(capsCache);
  const [policy, setPolicy] = useState<VoicePolicy>(() =>
    POLICIES.includes(profile?.policy as VoicePolicy)
      ? profile?.policy as VoicePolicy
      : "auto");
  const [voiceId, setVoiceId] = useState(String(profile?.voice_id ?? ""));
  const [fallback, setFallback] = useState(
    profile?.allow_local_fallback ?? true);
  const [applying, setApplying] = useState(false);
  const preview = useVoicePreview(workspaceId);

  useEffect(() => {
    let alive = true;
    void loadCapabilities().then((c) => { if (alive && c) setCaps(c); });
    return () => { alive = false; };
  }, []);

  const voices = caps?.tts?.voices ?? [];
  const voicesFor = (p: VoicePolicy) => voices.filter((v) =>
    p === "local" ? v.voice_id === LOCAL_VOICE_ID
    : p === "cloud" ? v.voice_id !== LOCAL_VOICE_ID
    : true);
  // 云端可用 ⇔ 已配置（capabilities.voices 只在 cloud_configured 时含云端音色）；
  // caps 未加载完毕前不禁用，避免可用项闪烁
  const cloudOk = caps == null
    || voices.some((v) => v.voice_id !== LOCAL_VOICE_ID);
  const localOk = caps == null || caps.tts?.local_enabled === true;

  const policyVoices = voicesFor(policy);
  // 当前音色不在所选策略候选内时回落到首个候选（render 期派生，不写状态）
  const effectiveVoiceId = policyVoices.some((v) => v.voice_id === voiceId)
    ? voiceId
    : policyVoices[0]?.voice_id ?? voiceId;
  const lessonLang: LessonLanguage =
    String(profile?.language ?? "").startsWith("en") ? "en" : "zh";

  const doPreview = () => {
    onPreviewStart?.();
    void preview.preview({
      language: lessonLang,
      voicePreferences: {
        policy,
        voice_id: effectiveVoiceId || undefined,
        allow_local_fallback: fallback,
      },
    }, effectiveVoiceId);
  };

  const apply = async () => {
    if (applying) return;
    setApplying(true);
    try {
      await onApply({
        policy,
        voice_id: policy === "silent" ? null : effectiveVoiceId || null,
        allow_local_fallback: fallback,
      });
    } finally {
      setApplying(false);
    }
  };

  return (
    <div role="dialog" aria-label={tr("cls.voice.title")}
         className="motion-pop voice-settings-dialog absolute bottom-full right-0 z-50 mb-3 w-80 max-w-[calc(100vw-24px)] rounded-2xl border border-border bg-surface p-5 shadow-xl">
      <div className="mb-4 flex items-center gap-2"><AudioLines size={17} className="text-accent" /><h2 className="flex-1 text-sm font-semibold text-fg">{tr("cls.voice.title")}</h2><button type="button" onClick={onClose} aria-label={tr("cls.play.close.panel")} className="flex h-7 w-7 items-center justify-center rounded-lg text-muted hover:bg-surface-hover"><X size={15} /></button></div>
      {caps?.tts?.reason ? (
        <p className="mb-2 text-[0.68rem] leading-snug text-muted">
          {caps.tts.available === false
            ? tr("cls.form.voice.unavailable").replace("%s", caps.tts.reason)
            : caps.tts.reason}
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-1.5" role="radiogroup"
           aria-label={tr("cls.form.voice")}>
        {POLICIES.map((p) => {
          const disabled = (p === "cloud" && !cloudOk)
            || (p === "local" && !localOk);
          return (
            <label key={p}
                   className={cn(
                     "flex min-h-14 items-center gap-2 rounded-xl border px-3 py-2 text-[11px] transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent",
                     disabled
                       ? "cursor-not-allowed border-border text-muted/50"
                       : policy === p
                         ? "border-accent bg-accent-soft/50 font-medium text-accent-strong"
                         : "cursor-pointer border-border text-fg-secondary hover:border-accent/40 hover:text-fg",
                   )}>
              <input type="radio" name="classroom-voice-policy"
                     value={p} checked={policy === p}
                     disabled={disabled}
                     onChange={() => {
                       setPolicy(p);
                       setVoiceId(voicesFor(p)[0]?.voice_id ?? "");
                     }}
                     className="sr-only" />
              {p === "cloud" ? <Cloud size={16} /> : p === "local" ? <Laptop size={16} /> : p === "silent" ? <VolumeX size={16} /> : <Sparkles size={16} />}
              {tr(`cls.form.voice.${p}`)}
            </label>
          );
        })}
      </div>
      {policy !== "silent" && policyVoices.length > 0 && (
        <div className="mt-2.5 flex items-center gap-1.5">
          <select value={effectiveVoiceId}
                  onChange={(e) => setVoiceId(e.target.value)}
                  aria-label={tr("cls.form.voice")}
                  className={cn(FIELD_CLS,
                                "h-8 min-w-0 flex-1 cursor-pointer text-xs")}>
            {policyVoices.map((v) => (
              <option key={v.voice_id} value={v.voice_id}>
                {v.display_name}
              </option>
            ))}
          </select>
          <button type="button" onClick={doPreview}
                  disabled={preview.previewing !== null}
                  className="inline-flex h-8 shrink-0 cursor-pointer items-center gap-1 rounded-[8px] border border-border px-2.5 text-xs text-fg-secondary transition-colors hover:border-accent/50 hover:text-fg disabled:cursor-not-allowed disabled:opacity-50">
            {preview.previewing === effectiveVoiceId
              ? <Loader2 size={12} className="animate-spin" />
              : <Play size={12} />}
            {tr("cls.voice.preview")}
          </button>
        </div>
      )}
      {preview.failed && (
        <p className="mt-1.5 text-[0.68rem] text-danger">
          {tr("cls.voice.preview.failed")}
        </p>
      )}
      {policy === "cloud" && (
        <label className="mt-2 flex cursor-pointer items-center gap-2 text-[0.7rem] text-fg-secondary">
          <input type="checkbox" checked={fallback}
                 onChange={(e) => setFallback(e.target.checked)}
                 className="size-3.5 cursor-pointer accent-accent" />
          {tr("cls.form.voice.fallback")}
        </label>
      )}
      <button type="button" onClick={() => void apply()}
              disabled={applying}
              className="mt-4 inline-flex h-10 w-full cursor-pointer items-center justify-center gap-1.5 rounded-[8px] bg-accent text-xs font-medium text-white transition-colors hover:bg-accent/85 disabled:opacity-50">
        {applying && <Loader2 size={12} className="animate-spin" />}
        {tr("cls.voice.apply")}
      </button>
    </div>
  );
}
