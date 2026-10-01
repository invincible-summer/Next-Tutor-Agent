"use client";

import Link from "next/link";
import { useState } from "react";
import { Copy, UserRound } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { AccountCard } from "@/components/pages/profile/AccountCard";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { gradeLabel } from "@/lib/i18n";
import { makePageT } from "@/lib/i18n-page";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { defaultClientState, currentRouteEpoch } from "@/lib/assistant/page-context";
import { navigationSucceeded } from "@/lib/assistant/navigation";
import { STRINGS as PROFILE_STRINGS } from "../profile/strings";
import { STRINGS } from "../settings/strings";

export default function AccountPage() {
  const lang = useUIStore((s) => s.lang);
  const user = useAuthStore((s) => s.user);
  const [copyState, setCopyState] = useState("");
  const [dirty, setDirty] = useState(false);
  const tr = makePageT(lang, STRINGS);
  const profileT = makePageT(lang, PROFILE_STRINGS);
  useUnsavedChanges(dirty, tr("settings.unsaved"));
  useAssistantPage({
    clientState: () => ({ ...defaultClientState(), dirty, blocking_activity: dirty ? "unsaved_editor" : "none" }),
    beforeNavigate: async () => !dirty || window.confirm(tr("settings.unsaved")) ? "allow" : "stay",
    context: () => ({ schema_version: 1, route_id: "account", route_epoch: currentRouteEpoch() }),
    navigationStatus: (target) => target.kind === "module" || (target.kind === "profile_section" && target.section === "account") ? navigationSucceeded : null,
  });
  const rows = user ? [
    [profileT("account.name"), user.profile.name || tr("account.empty")],
    [profileT("account.grade"), gradeLabel(lang, user.profile.grade)],
    [profileT("account.school"), user.profile.school || tr("account.empty")],
    [profileT("account.subjects"), user.profile.subjects?.join("、") || tr("account.empty")],
  ] : [];
  const date = (value: number) => value > 0 && Number.isFinite(value) ? new Date(value * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US") : tr("account.noDate");
  const accountRows = user ? [
    [tr("account.username"), user.username], [tr("account.email"), user.email],
    [tr("account.created"), date(user.created_at)], [tr("account.lastLogin"), date(user.last_login_at)],
  ] : [];
  const renderRows = (items: string[][]) => <dl className="divide-y divide-border-light">{items.map(([label, value]) => <div key={label} className="flex gap-6 py-3 text-sm"><dt className="w-28 shrink-0 text-muted">{label}</dt><dd className="min-w-0 break-words text-fg-secondary">{value}</dd></div>)}</dl>;
  return <div className="h-full overflow-y-auto p-6 page-in">
    <div className="mx-auto flex max-w-[960px] flex-col gap-5" data-account-section="account">
      <header><h1 className="font-serif text-xl font-semibold text-fg">{tr("account.title")}</h1><p className="mt-1 text-xs text-muted">{tr("account.subtitle")}</p></header>
      {!user ? <Card><div className="flex items-center gap-3"><UserRound className="text-accent" size={24} /><div><p className="font-medium text-fg">{tr("account.notLoggedIn")}</p><p className="mt-1 text-xs text-muted">{tr("account.loginHint")}</p></div></div><Link href="/login?redirect=%2Faccount" className="mt-5 inline-flex rounded-lg bg-accent px-4 py-2 text-sm text-white">{tr("account.login")}</Link></Card> : <>
        <AccountCard key={user.id} tr={profileT} onDirtyChange={setDirty} />
        <Card><h2 className="font-serif font-semibold text-fg">{tr("account.personal")}</h2>{renderRows(rows)}</Card>
        <Card><div className="mb-1 flex items-center justify-between"><h2 className="font-serif font-semibold text-fg">{tr("account.details")}</h2><span className="text-xs text-muted">{tr(user.role === "admin" ? "account.role.admin" : "account.role.user")}</span></div>
          {renderRows(accountRows)}
          <div className="flex items-center gap-6 border-t border-border-light py-3 text-sm"><span className="w-28 shrink-0 text-muted">{tr("account.id")}</span><code className="min-w-0 break-all text-xs text-fg-secondary">{user.id}</code><Button variant="ghost" size="sm" title={tr("account.copy")} icon={<Copy size={13} />} onClick={async () => {
            try { await navigator.clipboard.writeText(user.id); setCopyState("account.copied"); } catch { setCopyState("account.copyFailed"); }
          }}>{tr("account.copy")}</Button></div>
          {copyState && <p role="status" className="text-xs text-muted">{tr(copyState)}</p>}
        </Card>
      </>}
    </div>
  </div>;
}
