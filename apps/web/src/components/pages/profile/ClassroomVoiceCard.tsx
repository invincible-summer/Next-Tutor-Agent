"use client";

/* 「课堂语音」默认偏好卡（prefs.classroom，plan.md §20.1）。
 *
 * 新建课堂 run 未显式指定语音时的个人默认（有效顺序：run 显式 > 课程 brief
 * > 个人默认 > 系统默认）。音色候选来自 GET /classroom/capabilities
 * （本地 = melo-zh，其余为云端批准音色；不可用后端按 VoicePanel 约定禁用）。
 * 课堂总闸关闭（caps.enabled === false）显示不可用提示；游客不提供账户偏好。
 * 试听仅课堂播放器提供（voice-preview 端点是 workspace 作用域），本卡不含。
 * 保存走 PUT /user/profile：classroom 层是整层替换式合并，必须携带既有键
 * （theme_id/captions 等）再改语音三键，避免清掉其他课堂偏好。 */
import { useEffect, useId, useState } from "react";
import { AudioLines, Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Hint } from "@/components/ui/Hint";
import { useToast } from "@/components/ui/Toast";
import { Input, FIELD_CLS } from "@/components/ui/Input";
import { getClassroomCapabilities } from "@/lib/api-classroom";
import { updateUserProfile } from "@/lib/api-modules";
import { useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/settings/strings";
import type {
  ClassroomCapabilities, VoicePolicy,
} from "@/lib/types-classroom.generated";
import type { ClassroomPrefs } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

/** 后端音色约定：本地 MeloTTS 固定 voice_id（与课堂 VoicePanel 一致）。 */
const LOCAL_VOICE_ID = "melo-zh";
const POLICIES: VoicePolicy[] = ["auto", "cloud", "local", "silent"];

export function ClassroomVoiceCard({ tr, onDirtyChange }: { tr: Tr; onDirtyChange?: (dirty: boolean) => void }) {
  const lang = useUIStore((s) => s.lang);
  const st = makePageT(lang, STRINGS);
  const notify = useToast();
  const voiceIdField = useId();
  const help = (label: string, text: string) => <Hint label={st("settings.help").replace("{label}", label)} text={text} align="end" />;
  const user = useAuthStore((s) => s.user);
  const saved = user?.profile.prefs?.classroom;
  const [policy, setPolicy] = useState<VoicePolicy>(() =>
    POLICIES.includes(saved?.voice_policy as VoicePolicy)
      ? saved?.voice_policy as VoicePolicy
      : "auto");
  const [voiceId, setVoiceId] = useState(saved?.voice_id ?? "");
  const [fallback, setFallback] = useState(saved?.allow_local_fallback ?? true);
  const [caps, setCaps] = useState<ClassroomCapabilities | null>(null);
  const [capsFailed, setCapsFailed] = useState(false);
  const [capsRetry, setCapsRetry] = useState(0);
  const [saving, setSaving] = useState(false);
  const [savedOk, setSavedOk] = useState(false);

  useEffect(() => {
    if (!user) return;
    let alive = true;
    getClassroomCapabilities()
      .then((c) => { if (alive) setCaps(c); })
      .catch(() => { if (alive) setCapsFailed(true); });
    return () => { alive = false; };
  }, [user, capsRetry]);

  const dirty = !savedOk && (policy !== (saved?.voice_policy ?? "auto")
    || voiceId !== (saved?.voice_id ?? "") || fallback !== (saved?.allow_local_fallback ?? true));
  useEffect(() => {
    onDirtyChange?.(dirty || saving);
    return () => onDirtyChange?.(false);
  }, [dirty, saving, onDirtyChange]);

  if (!user) return null;
  if (caps?.enabled === false) return <Card><p className="text-sm text-muted">{tr("account.classroomVoice.disabled")}</p></Card>;   // 课堂总闸/灰度关闭

  const voices = caps?.tts?.voices ?? [];
  const voicesFor = (p: VoicePolicy) => voices.filter((v) =>
    p === "local" ? v.voice_id === LOCAL_VOICE_ID
    : p === "cloud" ? v.voice_id !== LOCAL_VOICE_ID
    : p === "auto" ? (caps?.tts?.local_enabled ? v.voice_id === LOCAL_VOICE_ID : v.voice_id !== LOCAL_VOICE_ID)
    : true).sort((a, b) => Number(b.voice_id === LOCAL_VOICE_ID) - Number(a.voice_id === LOCAL_VOICE_ID));
  // 云端可用 ⇔ capabilities.voices 含云端音色；caps 未加载前不禁用（避免闪烁）
  const cloudOk = caps == null
    || voices.some((v) => v.voice_id !== LOCAL_VOICE_ID);
  const localOk = caps == null || caps.tts?.local_enabled === true;

  const policyVoices = voicesFor(policy);
  // 已存音色不在所选策略候选内时回落首个候选（render 期派生，不写状态）
  const effectiveVoiceId = policyVoices.some((v) => v.voice_id === voiceId)
    ? voiceId
    : policyVoices.find((v) => v.voice_id !== LOCAL_VOICE_ID || localOk)?.voice_id ?? voiceId;

  const save = async () => {
    if (saving) return;
    setSaving(true);
    setSavedOk(false);
    const classroom: ClassroomPrefs = {
      ...(user.profile.prefs?.classroom ?? {}),
      voice_policy: policy,
      allow_local_fallback: fallback,
    };
    if (policy !== "silent") {
      // 后端要求 voice_id 非空字符串；无候选时删除该键而不是发空串
      if (effectiveVoiceId) classroom.voice_id = effectiveVoiceId;
      else delete classroom.voice_id;
    }
    try {
      const profile = await updateUserProfile({ prefs: { classroom } });
      useAuthStore.setState((state) => state.user?.id === user.id ? { user: { ...state.user, profile } } : {});
      setSavedOk(true);
      notify(tr("account.classroomVoice.saved"));
    } catch {
      notify(tr("account.classroomVoice.failed"), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card aria-busy={saving}>
      <CardHeader
        icon={<AudioLines size={16} />}
        title={<span className="flex items-center gap-2">{tr("account.classroomVoice")}{help(tr("account.classroomVoice"), tr("account.classroomVoice.desc"))}</span>}
      />
      {capsFailed && (
        <div className="mb-2 flex items-center gap-2">
          <span className="text-[0.7rem] text-danger">
            {tr("account.classroomVoice.unavailable")}
          </span>
          <Button variant="ghost" size="sm"
                  onClick={() => {
                    setCapsFailed(false);
                    setCapsRetry((n) => n + 1);
                  }}>
            {tr("account.classroomVoice.retry")}
          </Button>
          {help(tr("account.classroomVoice.retry"), st("settings.retry.hint"))}
        </div>
      )}
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4"
           role="radiogroup" aria-label={tr("account.classroomVoice")}>
        {POLICIES.map((p) => {
          const disabled = (p === "cloud" && !cloudOk)
            || (p === "local" && !localOk);
          return (
            <div key={p}
                   className={cn(
                     "flex min-h-8 items-center gap-1 rounded-[8px] border pr-2 text-[0.7rem] transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent",
                     disabled
                       ? "cursor-not-allowed border-border text-muted/50"
                       : policy === p
                         ? "border-accent bg-accent-soft/50 font-medium text-accent-strong"
                         : "cursor-pointer border-border text-fg-secondary hover:border-accent/40 hover:text-fg",
                   )}>
              <label className="flex min-h-8 flex-1 items-center justify-center px-1.5"><Input type="radio" name="profile-classroom-voice-policy"
                     value={p} checked={policy === p}
                     disabled={disabled}
                     onChange={() => {
                       setPolicy(p);
                       setVoiceId(voicesFor(p)[0]?.voice_id ?? "");
                       setSavedOk(false);
                     }}
                     className="sr-only" />
              {tr(`account.classroomVoice.${p}`)}
              </label>{help(tr(`account.classroomVoice.${p}`), st(`settings.voice.${p}.hint`))}
            </div>
          );
        })}
      </div>
      {policy !== "silent" && policyVoices.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 flex items-center gap-2 text-[0.68rem] text-muted">
            <label htmlFor={voiceIdField}>{tr("account.classroomVoice.voice")}</label>{help(tr("account.classroomVoice.voice"), st("settings.voice.voice.hint"))}
          </div>
          <select id={voiceIdField} value={effectiveVoiceId}
                  onChange={(e) => {
                    setVoiceId(e.target.value);
                    setSavedOk(false);
                  }}
                  className={cn(FIELD_CLS,
                                "h-8 cursor-pointer text-xs sm:max-w-sm")}>
            {policyVoices.map((v) => {
              const isLocal = v.voice_id === LOCAL_VOICE_ID;
              return (
                <option key={v.voice_id} value={v.voice_id}
                        disabled={isLocal ? !localOk : !cloudOk}>
                  {v.display_name} · {tr(isLocal
                    ? "account.classroomVoice.tag.local"
                    : "account.classroomVoice.tag.cloud")}
                </option>
              );
            })}
          </select>
        </div>
      )}
      {policy === "cloud" && (
        <div className="mt-2.5 flex items-center gap-2"><label className="flex cursor-pointer items-center gap-2 text-[0.7rem] text-fg-secondary">
          <Input type="checkbox" checked={fallback}
                 onChange={(e) => {
                   setFallback(e.target.checked);
                   setSavedOk(false);
                 }}
                 className="size-3.5 cursor-pointer accent-accent" />
          {tr("account.classroomVoice.fallback")}
        </label>{help(tr("account.classroomVoice.fallback"), st("settings.voice.fallback.hint"))}</div>
      )}
      <div className="mt-3 flex items-center justify-end gap-2 border-t border-border-light pt-3">
        <Button demoWrite size="sm"
                icon={saving
                  ? <Loader2 size={12} className="animate-spin" />
                  : <Check size={12} />}
                disabled={saving}
                onClick={() => void save()}>
          {saving ? tr("account.saving") : tr("account.save")}
        </Button>
        {help(tr("account.save"), st("settings.voice.save.hint"))}
      </div>
    </Card>
  );
}
