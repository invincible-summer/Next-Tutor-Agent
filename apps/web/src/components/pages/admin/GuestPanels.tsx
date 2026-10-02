"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Users, Trash2 } from "lucide-react";
import { getAdminGuestPolicy, setAdminGuestPolicy, scanAdminGuestData, purgeAdminGuestData, type AdminGuestData } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { fmtBytes } from "@/lib/format";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ConfirmModal } from "@/components/ui/Modal";
import type { Tr } from "./Field";

export function GuestPolicyPanel({ tr }: { tr: Tr }) {
  const [allowed, setAllowed] = useState(false);
  const [saved, setSaved] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let alive = true;
    void getAdminGuestPolicy().then((p) => { if (alive) { setAllowed(p.allow_guests); setSaved(p.allow_guests); } })
      .catch(() => { if (alive) setNotice("adm.guest.failed"); });
    return () => { alive = false; };
  }, []);
  async function save() {
    if (busy) return;
    setBusy(true); setNotice("");
    try {
      const p = await setAdminGuestPolicy(allowed);
      setAllowed(p.allow_guests); setSaved(p.allow_guests); setNotice("adm.guest.saved");
      await useAuthStore.getState().fetchStatus();
    } catch { setNotice("adm.guest.failed"); }
    finally { setBusy(false); }
  }
  return <Card>
    <CardHeader icon={<Users size={16} />} title={tr("adm.guest.title")} desc={tr("adm.guest.desc")} />
    <div className="flex flex-wrap items-center justify-between gap-4">
      <label className="flex items-center gap-2 text-sm text-fg"><Input type="checkbox" checked={allowed} disabled={busy || saved === null} onChange={(e) => setAllowed(e.target.checked)} />{tr("adm.guest.allow")}</label>
      <Button size="sm" onClick={() => void save()} disabled={busy || saved === null || saved === allowed}>{tr(busy ? "adm.guest.saving" : "adm.guest.save")}</Button>
    </div>
    <p className="mt-3 text-xs leading-5 text-muted">{tr("adm.guest.boundaries")}</p>
    {notice && <p role="status" className="mt-3 text-xs text-fg">{tr(notice)}</p>}
  </Card>;
}

export function GuestCleanupPanel({ tr }: { tr: Tr }) {
  const [report, setReport] = useState<AdminGuestData | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    try { setReport(await scanAdminGuestData()); }
    catch { setNotice(tr("adm.guest.failed")); }
    finally { setLoading(false); }
  }, [tr]);
  // makePageT returns a new function on every render; reload only on mount.
  useEffect(() => { let alive = true; queueMicrotask(() => { if (alive) void load(); }); return () => { alive = false; }; }, []); // eslint-disable-line react-hooks/exhaustive-deps
  async function purge() {
    if (busy) return;
    setBusy(true);
    try {
      const result = await purgeAdminGuestData();
      setConfirming(false);
      setNotice(tr(result.status === "partial" ? "adm.guest.partial" : "adm.guest.purged")
        .replace("{visitors}", String(result.active.visitors)).replace("{count}", String(result.total_deleted))
        .replace("{size}", fmtBytes(result.total_bytes)).replace("{failed}", String(result.failed)));
      await load();
    } catch { setNotice(tr("adm.guest.failed")); }
    finally { setBusy(false); }
  }
  return <Card>
    <CardHeader icon={<Trash2 size={16} />} title={tr("adm.guest.cleanup")} desc={tr("adm.guest.cleanupDesc")}
      right={<Button size="sm" variant="outline" disabled={busy || loading} icon={<RefreshCw size={12} />} onClick={() => void load()}>{tr("adm.cleanup.rescan")}</Button>} />
    {report && <div className="grid grid-cols-3 gap-3 text-xs">
      <div className="rounded-lg bg-surface p-3 text-muted">{tr("adm.guest.active")}<p className="mt-1 text-lg text-fg">{report.active.visitors}</p></div>
      <div className="rounded-lg bg-surface p-3 text-muted">{tr("adm.guest.tasks")}<p className="mt-1 text-lg text-fg">{report.active.tasks}</p></div>
      <div className="rounded-lg bg-surface p-3 text-muted">{tr("adm.guest.legacy")}<p className="mt-1 text-lg text-fg">{report.total_items} <span className="text-xs text-muted">/ {fmtBytes(report.total_bytes)}</span></p></div>
    </div>}
    {notice && <p role="status" className="mt-3 text-xs text-fg">{notice}</p>}
    <div className="mt-4 flex justify-end"><Button variant="danger" size="sm" disabled={busy || loading || !report || (!report.total_items && !report.active.visitors)} onClick={() => setConfirming(true)}>{tr("adm.guest.purge")}</Button></div>
    <ConfirmModal open={confirming} onClose={() => { if (!busy) setConfirming(false); }} onConfirm={() => void purge()}
      title={tr("adm.guest.purge")} desc={tr("adm.guest.confirm")} confirmText={tr(busy ? "adm.guest.purging" : "adm.guest.purge")} cancelText={tr("adm.users.cancel")} />
  </Card>;
}
