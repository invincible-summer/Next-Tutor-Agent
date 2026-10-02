"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { UserAvatar } from "@/components/auth/UserAvatar";
import { AvatarEditor } from "./AvatarEditor";
import { Check, Mail, Pencil, X } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { updateUserProfile } from "@/lib/api-modules";
import { useAuthStore } from "@/lib/auth-store";
import { gradeLabel } from "@/lib/i18n";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/settings/strings";

import { Input } from "@/components/ui/Input";

type Tr = (key: string, fallback?: string) => string;

/** Account identity and profile editing; preferences live on the settings page. */
export function AccountCard({ tr, onDirtyChange }: { tr: Tr; onDirtyChange?: (dirty: boolean) => void }) {
  const lang = useUIStore((s) => s.lang);
  const user = useAuthStore((s) => s.user);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const [avatarDirty, setAvatarDirty] = useState(false);
  const [form, setForm] = useState<{ name: string; school: string; subjects: string }>({ name: "", school: "", subjects: "" });
  const st = makePageT(lang, STRINGS);
  const notify = useToast();
  const dirty = editing && !!user && (form.name !== (user.profile.name || "")
    || form.school !== (user.profile.school || "")
    || form.subjects !== (user.profile.subjects || []).join(", "));
  useEffect(() => {
    onDirtyChange?.(dirty || saving || avatarDirty);
    return () => onDirtyChange?.(false);
  }, [dirty, saving, avatarDirty, onDirtyChange]);
  const closeEdit = () => { if (!saving && (!dirty || window.confirm(st("settings.unsaved")))) setEditing(false); };
  if (!user) return null;
  const p = user.profile;

  const startEdit = () => {
    setForm({
      name: p.name || "",
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
        school: form.school.trim(),
        subjects: form.subjects.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      });
      // Refresh the in-memory auth user so TopBar & co. see the new profile.
      useAuthStore.setState((state) => state.user?.id === user.id ? { user: { ...state.user, profile } } : {});
      setEditing(false);
      notify(st("settings.saved"));
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <div className="flex items-center gap-4">
        <UserAvatar user={user} className="h-14 w-14 text-xl" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-serif text-lg font-semibold text-fg">{p.name || user.username}</span>
            <Badge tone="accent">{gradeLabel(lang, p.grade)}</Badge>
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
          <Button demoWrite variant="outline" size="sm" icon={<Pencil size={12} />} onClick={startEdit}>
            {tr("account.edit")}
          </Button>
        )}
      </div>

      <div className="mt-3 text-xs text-muted">{st("settings.gradeLocation")} <Link href="/settings?section=learning" className="text-accent hover:underline">{st("settings.learning")}</Link></div>
      <AvatarEditor key={user.id} onDirtyChange={setAvatarDirty} />

      <Modal open={editing} onClose={closeEdit} title={tr("account.edit")}>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-[0.68rem] text-muted">{tr("account.name")}</span>
              <Input value={form.name} maxLength={40}
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label className="block">
              <span className="mb-1 block text-[0.68rem] text-muted">{tr("account.school")}</span>
              <Input value={form.school} maxLength={80}
                onChange={(e) => setForm({ ...form, school: e.target.value })} />
            </label>
            <label className="block">
              <span className="mb-1 block text-[0.68rem] text-muted">
                {tr("account.subjects")} · {tr("account.subjects.hint")}
              </span>
              <Input value={form.subjects}
                onChange={(e) => setForm({ ...form, subjects: e.target.value })} />
            </label>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <Button demoWrite size="sm" icon={<Check size={12} />} disabled={saving} onClick={save}>
              {saving ? tr("account.saving") : tr("account.save")}
            </Button>
            <Button variant="ghost" size="sm" icon={<X size={12} />} disabled={saving} onClick={closeEdit}>
              {tr("account.cancel")}
            </Button>
            {failed && <span className="text-[0.7rem] text-danger">{tr("account.save.failed")}</span>}
          </div>
      </Modal>
    </Card>
  );
}
