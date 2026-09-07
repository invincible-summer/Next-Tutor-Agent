"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Mail, Pencil, Trash2, UserRound, X } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Modal } from "@/components/ui/Modal";
import { deleteAccount, updateUserProfile } from "@/lib/api-modules";
import { useAuthStore } from "@/lib/auth-store";
import { AUTO_GRADE, gradeForApi, gradeFromApi, type Grade } from "@/lib/types";
import { cn } from "@/lib/cn";

type Tr = (key: string, fallback?: string) => string;

// 含 P1「自动」token；后端空串 = 自动，经 gradeFromApi/gradeForApi 互转。
const GRADES = ["自动", "小学", "初中", "高中", "本科"] as const satisfies readonly Grade[];

const INPUT =
  "w-full rounded-[8px] border border-border bg-surface px-2.5 py-1.5 text-xs text-fg outline-none transition-colors focus:border-accent";

/** M0 账户卡：注册资料（身份基础设施），区别于 M2 学术画像。
 * 仅登录态渲染；学段修改经后端同步进 StudentModel。
 * 危险区：自助注销账号——密码 + 确认短语双重确认，成功后登出并回到游客态。 */
export function AccountCard({ tr }: { tr: Tr }) {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const clearAuth = useAuthStore((s) => s.clearAuth);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const [form, setForm] = useState<{ name: string; grade: Grade; school: string; subjects: string }>({ name: "", grade: AUTO_GRADE, school: "", subjects: "" });
  // 注销流程状态
  const [delOpen, setDelOpen] = useState(false);
  const [delPwd, setDelPwd] = useState("");
  const [delPhrase, setDelPhrase] = useState("");
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);
  // OCR 并行偏好开关
  const [ocrBusy, setOcrBusy] = useState(false);
  const [ocrFailed, setOcrFailed] = useState(false);
  // 朗读语速偏好（语音通话 TTS）：拖动是本地草稿，抬手才提交。
  const [speedDraft, setSpeedDraft] = useState<number | null>(null);
  const [speedBusy, setSpeedBusy] = useState(false);
  const [speedFailed, setSpeedFailed] = useState(false);

  if (!user) return null;
  const p = user.profile;

  const phrase = tr("account.delete.phrase");
  const canDelete = delPwd.length > 0 && delPhrase === phrase && !delBusy;
  // 未显式设置时与后端实例默认（PDF_OCR_CONCURRENCY>1）一致：视为开。
  const ocrParallel = p.prefs?.ocr_parallel ?? true;
  // 未显式设置时与后端实例默认（VOICE_TTS_SPEED=0.9）一致：视为 0.9。
  const ttsSpeed = speedDraft ?? p.prefs?.tts_speed ?? 0.9;

  const commitTtsSpeed = async (next: number) => {
    setSpeedBusy(true);
    setSpeedFailed(false);
    try {
      const profile = await updateUserProfile({ prefs: { tts_speed: next } });
      useAuthStore.setState({ user: { ...user, profile } });
      setSpeedDraft(null);
    } catch {
      setSpeedFailed(true);
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
    setOcrFailed(false);
    try {
      const profile = await updateUserProfile({ prefs: { ocr_parallel: next } });
      useAuthStore.setState({ user: { ...user, profile } });
    } catch {
      setOcrFailed(true);
    } finally {
      setOcrBusy(false);
    }
  };

  const startEdit = () => {
    setForm({
      name: p.name || "",
      grade: gradeFromApi(p.grade),
      school: p.school || "",
      subjects: (p.subjects || []).join(", "),
    });
    setFailed(false);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true);
    setFailed(false);
    try {
      const profile = await updateUserProfile({
        name: form.name.trim(),
        grade: gradeForApi(form.grade),
        school: form.school.trim(),
        subjects: form.subjects.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      });
      // Refresh the in-memory auth user so TopBar & co. see the new profile.
      useAuthStore.setState({ user: { ...user, profile } });
      setEditing(false);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
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
      clearAuth();
      router.push("/chat");
    } catch (e) {
      setDelError(
        e instanceof Error && e.message === "invalid_password"
          ? tr("account.delete.wrongPassword")
          : tr("account.delete.failed"),
      );
      setDelBusy(false);
    }
  };

  return (
    <Card>
      <div className="flex items-center gap-4">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-accent2-soft text-accent2">
          <UserRound size={28} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-serif text-lg font-semibold text-fg">{p.name || user.username}</span>
            <Badge tone="accent">{gradeFromApi(p.grade)}</Badge>
            {(p.subjects || []).map((s) => (
              <Badge key={s} tone="outline">{s}</Badge>
            ))}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted">
            <span className="flex items-center gap-1">
              <Mail size={11} /> {user.email}
            </span>
            {p.school && <span>{p.school}</span>}
            <span className="text-muted/60">{tr("account.desc")}</span>
          </div>
        </div>
        {!editing && (
          <Button variant="outline" size="sm" icon={<Pencil size={12} />} onClick={startEdit}>
            {tr("account.edit")}
          </Button>
        )}
      </div>

      {editing && (
        <div className="mt-4 border-t border-border-light pt-3">
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-[0.68rem] text-muted">{tr("account.name")}</span>
              <input className={INPUT} value={form.name} maxLength={40}
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label className="block">
              <span className="mb-1 block text-[0.68rem] text-muted">{tr("account.grade")}</span>
              <select className={cn(INPUT, "cursor-pointer")} value={form.grade}
                onChange={(e) => setForm({ ...form, grade: e.target.value as Grade })}>
                {GRADES.map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-[0.68rem] text-muted">{tr("account.school")}</span>
              <input className={INPUT} value={form.school} maxLength={80}
                onChange={(e) => setForm({ ...form, school: e.target.value })} />
            </label>
            <label className="block">
              <span className="mb-1 block text-[0.68rem] text-muted">
                {tr("account.subjects")} · {tr("account.subjects.hint")}
              </span>
              <input className={INPUT} value={form.subjects}
                onChange={(e) => setForm({ ...form, subjects: e.target.value })} />
            </label>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <Button size="sm" icon={<Check size={12} />} disabled={saving} onClick={save}>
              {saving ? tr("account.saving") : tr("account.save")}
            </Button>
            <Button variant="ghost" size="sm" icon={<X size={12} />} onClick={() => setEditing(false)}>
              {tr("account.cancel")}
            </Button>
            {failed && <span className="text-[0.7rem] text-danger">{tr("account.save.failed")}</span>}
          </div>
        </div>
      )}

      {/* 偏好区：教材 OCR 并行加速（每用户偏好，游客无此卡） */}
      <div className="mt-4 flex items-center justify-between gap-4 border-t border-border-light pt-3">
        <div className="min-w-0">
          <div className="text-xs font-medium text-fg">{tr("account.ocrParallel")}</div>
          <div className="mt-0.5 text-[0.68rem] leading-relaxed text-muted">
            {tr("account.ocrParallel.desc")}
            {ocrFailed && <span className="ml-1 text-danger">{tr("account.ocrParallel.failed")}</span>}
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={ocrParallel}
          disabled={ocrBusy}
          onClick={toggleOcrParallel}
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

      {/* 偏好区：朗读语速（语音通话，服务端按账号生效） */}
      <div className="mt-3 flex items-center justify-between gap-4 border-t border-border-light pt-3">
        <div className="min-w-0">
          <div className="text-xs font-medium text-fg">{tr("account.ttsSpeed")}</div>
          <div className="mt-0.5 text-[0.68rem] leading-relaxed text-muted">
            {tr("account.ttsSpeed.desc")}
            {speedFailed && <span className="ml-1 text-danger">{tr("account.ttsSpeed.failed")}</span>}
          </div>
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
            disabled={speedBusy}
            aria-label={tr("account.ttsSpeed")}
            onChange={(e) => setSpeedDraft(Number(e.target.value))}
            onPointerUp={releaseTtsSpeed}
            onKeyUp={(e) => {
              if (e.key === "ArrowLeft" || e.key === "ArrowRight") releaseTtsSpeed();
            }}
            className="h-1.5 w-32 cursor-pointer accent-[rgb(var(--accent))] disabled:cursor-not-allowed disabled:opacity-50 sm:w-40"
          />
        </div>
      </div>

      {/* 危险区：自助注销账号（特别确认） */}
      <div className="mt-4 flex items-center justify-between border-t border-border-light pt-3">
        <span className="text-[0.7rem] text-muted">{tr("account.danger")}</span>
        <Button variant="danger" size="sm" icon={<Trash2 size={12} />} onClick={openDelete}>
          {tr("account.delete")}
        </Button>
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
            <Button variant="danger" size="sm" disabled={!canDelete} onClick={doDelete}>
              {delBusy ? tr("account.delete.deleting") : tr("account.delete.submit")}
            </Button>
          </>
        }
      >
        <p className="text-xs leading-relaxed">{tr("account.delete.desc")}</p>
        <div className="mt-3 space-y-2.5">
          <label className="block">
            <span className="mb-1 block text-[0.68rem] text-muted">{tr("account.delete.password")}</span>
            <input type="password" className={INPUT} value={delPwd}
              onChange={(e) => setDelPwd(e.target.value)} autoComplete="current-password" />
          </label>
          <label className="block">
            <span className="mb-1 block text-[0.68rem] text-muted">{tr("account.delete.confirm.hint")}</span>
            <input className={INPUT} value={delPhrase} placeholder={phrase}
              onChange={(e) => setDelPhrase(e.target.value)} />
          </label>
          {delError && <p className="text-[0.7rem] text-danger">{delError}</p>}
        </div>
      </Modal>
    </Card>
  );
}
