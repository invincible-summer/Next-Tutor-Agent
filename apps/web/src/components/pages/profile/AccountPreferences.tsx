"use client";
import { DEMO_MODE, guardDemoAction } from "@/lib/demo";

import { useEffect, useId, useState } from "react";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Modal } from "@/components/ui/Modal";
import { Hint } from "@/components/ui/Hint";
import { useToast } from "@/components/ui/Toast";
import { deleteAccount, updateUserProfile } from "@/lib/api-modules";
import { useAuthStore } from "@/lib/auth-store";
import { illustrationMode as resolveIllustrationMode, type IllustrationMode } from "@/lib/api-illustrations";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/settings/strings";

import { FIELD_CLS, Input } from "@/components/ui/Input";

type Tr = (key: string, fallback?: string) => string;

export function AccountPreferences({ tr, section, onDirtyChange }: { tr: Tr; section: "processing" | "voice" | "account"; onDirtyChange?: (dirty: boolean) => void }) {
  const router = useRouter();
  const lang = useUIStore((s) => s.lang);
  const st = makePageT(lang, STRINGS);
  const notify = useToast();
  const passwordId = useId();
  const phraseId = useId();
  const help = (label: string, text: string) => <Hint label={st("settings.help").replace("{label}", label)} text={text} align="end" />;
  const user = useAuthStore((s) => s.user);
  const clearAuth = useAuthStore((s) => s.clearAuth);
  // 注销流程状态
  const [delOpen, setDelOpen] = useState(false);
  const [delPwd, setDelPwd] = useState("");
  const [delPhrase, setDelPhrase] = useState("");
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);
  // OCR 并行偏好开关
  const [ocrBusy, setOcrBusy] = useState(false);
  const [illustrationModeBusy, setIllustrationModeBusy] = useState(false);
  // 朗读语速偏好（语音通话 TTS）：拖动是本地草稿，抬手才提交。
  const [speedDraft, setSpeedDraft] = useState<number | null>(null);
  const [speedBusy, setSpeedBusy] = useState(false);
  const [speedFailed, setSpeedFailed] = useState(false);

  useEffect(() => {
    onDirtyChange?.(speedDraft !== null || speedBusy || ocrBusy || illustrationModeBusy || delBusy);
    return () => onDirtyChange?.(false);
  }, [speedDraft, speedBusy, ocrBusy, illustrationModeBusy, delBusy, onDirtyChange]);

  if (!user) return null;
  const p = user.profile;

  const phrase = tr("account.delete.phrase");
  const canDelete = delPwd.length > 0 && delPhrase === phrase && !delBusy;
  // 未显式设置时与后端实例默认（PDF_OCR_CONCURRENCY>1）一致：视为开。
  const ocrParallel = p.prefs?.ocr_parallel ?? true;
  const illustrationMode = resolveIllustrationMode(p.prefs?.quiz_illustration_mode);
  // 未显式设置时与后端实例默认（VOICE_TTS_SPEED=0.9）一致：视为 0.9。
  const ttsSpeed = speedDraft ?? p.prefs?.tts_speed ?? 0.9;

  const commitTtsSpeed = async (next: number) => {
    setSpeedBusy(true);
    setSpeedFailed(false);
    try {
      const profile = await updateUserProfile({ prefs: { tts_speed: next } });
      useAuthStore.setState((state) => state.user?.id === user.id ? { user: { ...state.user, profile } } : {});
      setSpeedDraft(null);
      notify(tr("account.saved"));
    } catch {
      setSpeedFailed(true);
      notify(tr("account.ttsSpeed.failed"), "error");
    } finally {
      setSpeedBusy(false);
    }
  };

  const releaseTtsSpeed = () => {
    if (speedDraft === null || speedDraft === p.prefs?.tts_speed) return;
    void commitTtsSpeed(speedDraft);
  };

  const toggleOcrParallel = async () => {
    const next = !ocrParallel;
    setOcrBusy(true);
    try {
      const profile = await updateUserProfile({ prefs: { ocr_parallel: next } });
      useAuthStore.setState((state) => state.user?.id === user.id ? { user: { ...state.user, profile } } : {});
      notify(tr("account.saved"));
    } catch {
      notify(tr("account.ocrParallel.failed"), "error");
    } finally {
      setOcrBusy(false);
    }
  };

  const changeIllustrationMode = async (next: IllustrationMode) => {
    if (next === illustrationMode) return;
    setIllustrationModeBusy(true);
    try {
      const profile = await updateUserProfile({ prefs: { quiz_illustration_mode: next } });
      useAuthStore.setState((state) => state.user?.id === user.id ? { user: { ...state.user, profile } } : {});
      notify(tr("account.saved"));
    } catch {
      notify(st("account.illustrationMode.failed"), "error");
    } finally {
      setIllustrationModeBusy(false);
    }
  };

  const openDelete = () => {
    setDelPwd("");
    setDelPhrase("");
    setDelError(null);
    setDelOpen(true);
  };

  const doDelete = async () => {
    setDelBusy(true);
    setDelError(null);
    try {
      await deleteAccount(delPwd);
      // 账户记录已删除，JWT 随即失效——本地登出并回到游客态工作区。
      if (useAuthStore.getState().user?.id === user.id) {
        clearAuth();
        router.push("/chat");
      }
    } catch (e) {
      setDelError(
        e instanceof Error && e.message === "invalid_password"
          ? tr("account.delete.wrongPassword")
          : tr("account.delete.failed"),
      );
      setDelBusy(false);
    }
  };

  return <Card aria-busy={ocrBusy || speedBusy}>
{section === "processing" && <>
      {/* 偏好区：教材 OCR 并行加速（每用户偏好，游客无此卡） */}
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-medium text-fg">{tr("account.ocrParallel")}{help(tr("account.ocrParallel"), tr("account.ocrParallel.desc"))}</div>
        </div>
        <button
          type="button"
          role="switch"
          aria-label={tr("account.ocrParallel")}
          aria-checked={ocrParallel}
          disabled={ocrBusy || speedBusy}
          onClick={guardDemoAction(toggleOcrParallel)}
          className={cn(
            "relative h-5.5 w-10 shrink-0 cursor-pointer rounded-full transition-colors",
            "disabled:cursor-not-allowed disabled:opacity-50",
            ocrParallel ? "bg-accent" : "bg-border",
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 h-4.5 w-4.5 rounded-full bg-surface shadow-sm transition-all",
              ocrParallel ? "left-5" : "left-0.5",
            )}
          />
        </button>
      </div>

      <div className="mt-4 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-medium text-fg">
            {st("account.illustrationMode")}{help(st("account.illustrationMode"), st("account.illustrationMode.desc"))}
          </div>
          <p className="mt-1 text-xs leading-5 text-muted">{st("account.illustrationMode.desc")}</p>
        </div>
        <select className={`${FIELD_CLS} !w-auto shrink-0 text-xs`}
          aria-label={st("account.illustrationMode")} value={illustrationMode}
          disabled={illustrationModeBusy || ocrBusy || speedBusy}
          onChange={(event) => void changeIllustrationMode(resolveIllustrationMode(event.target.value))}>
          <option value="v1">V1 · {st("account.illustrationMode.v1")}</option>
          <option value="v2">V2 · {st("account.illustrationMode.v2")}</option>
          <option value="v3">V3 · {st("account.illustrationMode.v3")}</option>
        </select>
      </div>

</>}
{section === "voice" && <>
      {/* 偏好区：朗读语速（语音通话，服务端按账号生效） */}
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-medium text-fg">{tr("account.ttsSpeed")}{help(tr("account.ttsSpeed"), tr("account.ttsSpeed.desc"))}</div>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <span className="tnum w-9 text-right text-[0.7rem] text-muted">
            {Math.round(ttsSpeed * 100)}%
          </span>
          <input
            type="range"
            min={0.5}
            max={1.5}
            step={0.05}
            value={ttsSpeed}
            disabled={DEMO_MODE || speedBusy}
            aria-label={tr("account.ttsSpeed")}
            onChange={(e) => setSpeedDraft(Number(e.target.value))}
            onPointerUp={releaseTtsSpeed}
            onKeyUp={(e) => {
              if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(e.key)) releaseTtsSpeed();
            }}
            className="h-1.5 w-32 cursor-pointer accent-[rgb(var(--accent))] disabled:cursor-not-allowed disabled:opacity-50 sm:w-40"
          />
        </div>
      </div>
      {speedFailed && <div className="mt-3 flex items-center gap-2"><Button demoWrite variant="outline" size="sm" disabled={speedBusy} onClick={releaseTtsSpeed}>{tr("account.save")}</Button>{help(tr("account.save"), st("settings.speed.retry.hint"))}</div>}

</>}
{section === "account" && <>
      {/* 危险区：自助注销账号（特别确认） */}
      <div className="flex items-center justify-between gap-4">
        <span className="text-[0.7rem] text-muted">{tr("account.danger")}</span>
        <div className="flex items-center gap-2"><Button demoWrite variant="danger" size="sm" icon={<Trash2 size={12} />} onClick={openDelete}>
          {tr("account.delete")}
        </Button>{help(tr("account.delete"), tr("account.delete.desc"))}</div>
      </div>

      <Modal
        open={delOpen}
        onClose={() => { if (!delBusy) setDelOpen(false); }}
        title={<span className="text-danger">{tr("account.delete.title")}</span>}
        footer={
          <>
            <Button variant="ghost" size="sm" disabled={delBusy} onClick={() => setDelOpen(false)}>
              {tr("account.cancel")}
            </Button>
            {help(tr("account.cancel"), st("settings.delete.cancel.hint"))}
            <Button demoWrite variant="danger" size="sm" disabled={!canDelete} onClick={doDelete}>
              {delBusy ? tr("account.delete.deleting") : tr("account.delete.submit")}
            </Button>
            {help(tr("account.delete.submit"), tr("account.delete.desc"))}
          </>
        }
      >
        <p className="text-xs leading-relaxed">{tr("account.delete.desc")}</p>
        <div className="mt-3 space-y-2.5">
          <div>
            <div className="mb-1 flex items-center gap-2 text-[0.68rem] text-muted"><label htmlFor={passwordId}>{tr("account.delete.password")}</label>{help(tr("account.delete.password"), st("settings.delete.password.hint"))}</div>
            <Input id={passwordId} type="password" value={delPwd}
              onChange={(e) => setDelPwd(e.target.value)} autoComplete="current-password" />
          </div>
          <div>
            <div className="mb-1 flex items-center gap-2 text-[0.68rem] text-muted"><label htmlFor={phraseId}>{tr("account.delete.confirm.hint")}</label>{help(tr("account.delete.confirm.hint"), st("settings.delete.confirm.hint"))}</div>
            <Input id={phraseId} value={delPhrase} placeholder={phrase}
              onChange={(e) => setDelPhrase(e.target.value)} />
          </div>
          {delError && <p className="text-[0.7rem] text-danger">{delError}</p>}
        </div>
      </Modal></>}
  </Card>;
}
