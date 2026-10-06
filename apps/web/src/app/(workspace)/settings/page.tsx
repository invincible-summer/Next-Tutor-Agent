"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { SlidersHorizontal, BookOpen, AudioLines, Sparkles, ScanLine, Shield, Info } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Hint } from "@/components/ui/Hint";
import { PageSkeleton } from "@/components/ui/EmptyState";
import { LocalSettings } from "@/components/pages/settings/LocalSettings";
import { AccountPreferences } from "@/components/pages/profile/AccountPreferences";
import { ClassroomVoiceCard } from "@/components/pages/profile/ClassroomVoiceCard";
import { AssistantSettings } from "@/components/assistant/AssistantSettings";
import { useAuthStore } from "@/lib/auth-store";
import { useAssistantStore } from "@/lib/assistant/store";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { t } from "@/lib/i18n";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { defaultClientState } from "@/lib/assistant/page-context";
import { cn } from "@/lib/cn";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { navigationAnchor, navigationSucceeded } from "@/lib/assistant/navigation";
import { STRINGS as PROFILE_STRINGS } from "../profile/strings";
import { STRINGS } from "./strings";

const SECTIONS = [
  { id: "general", icon: SlidersHorizontal }, { id: "learning", icon: BookOpen },
  { id: "voice", icon: AudioLines }, { id: "assistant", icon: Sparkles },
  { id: "processing", icon: ScanLine }, { id: "account", icon: Shield }, { id: "about", icon: Info },
] as const;

function SettingsContent() {
  const [voiceDirty, setVoiceDirty] = useState(false);
  const [preferenceDirty, setPreferenceDirty] = useState(false);
  const dirty = voiceDirty || preferenceDirty;
  const lang = useUIStore((s) => s.lang);
  const user = useAuthStore((s) => s.user);
  const caps = useAssistantStore((s) => s.capabilities);
  const capsError = useAssistantStore((s) => s.capsError);
  const refreshCapabilities = useAssistantStore((s) => s.refreshCapabilities);
  const params = useSearchParams();
  const section = SECTIONS.find((item) => item.id === params.get("section"))?.id ?? "general";
  const tr = makePageT(lang, STRINGS);
  const pt = makePageT(lang, PROFILE_STRINGS);
  useUnsavedChanges(dirty, tr("settings.unsaved"));
  useAssistantPage({
    clientState: () => ({ ...defaultClientState(), dirty, blocking_activity: dirty ? "unsaved_editor" : "none" }),
    beforeNavigate: async () => !dirty || window.confirm(tr("settings.unsaved")) ? "allow" : "stay",
    context: () => ({ schema_version: 1, route_id: "settings", route_epoch: currentRouteEpoch(), view: section }),
    navigationStatus: (target) => {
      if (target.kind === "module") return navigationSucceeded;
      if (target.kind === "settings_section" || target.kind === "profile_section") return navigationAnchor("settings-section", target.section || "general");
      return null;
    },
  });
  const needsAccount = section === "voice" || section === "assistant" || section === "processing" || section === "account";
  return <div className="h-full overflow-y-auto p-6 page-in">
    <div className="mx-auto max-w-[1100px]">
      <header className="mb-6"><h1 className="font-serif text-xl font-semibold text-fg">{t(lang, "settings.title")}</h1><p className="mt-0.5 text-xs text-muted">{tr("settings.desc")}</p></header>
      <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-7">
        <nav aria-label={t(lang, "settings.title")} className="flex flex-row flex-wrap gap-1 sm:sticky sm:top-0 sm:flex-col">
          {SECTIONS.map(({ id, icon: Icon }) => <Link key={id} href={`/settings?section=${id}`} aria-current={id === section ? "page" : undefined}
            className={cn("flex min-h-10 items-center gap-3 rounded-xl px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent", section === id ? "bg-accent-soft font-medium text-accent-strong" : "text-fg-secondary hover:bg-surface-hover")}><Icon size={16} aria-hidden="true" />{tr(`settings.${id}`)}</Link>)}
        </nav>
        <section key={`${section}:${user?.id ?? "guest"}`} data-settings-section={section} className="min-w-0 space-y-4" aria-label={tr(`settings.${section}`)}>
          <h2 className="font-serif text-lg font-semibold text-fg">{tr(`settings.${section}`)}</h2>
          {needsAccount && !user ? <Card><p className="text-sm text-muted">{tr("settings.loginHint")}</p><Link href={`/login?redirect=${encodeURIComponent(`/settings?section=${section}`)}`} className="mt-4 inline-block text-sm text-accent">{tr("account.login")}</Link></Card> : <>
            {(section === "general" || section === "learning" || section === "about") && <LocalSettings section={section} onDirtyChange={setPreferenceDirty} />}
            {(section === "voice" || section === "processing" || section === "account") && <AccountPreferences section={section} tr={pt} onDirtyChange={setPreferenceDirty} />}
            {section === "voice" && <ClassroomVoiceCard tr={pt} onDirtyChange={setVoiceDirty} />}
            {section === "assistant" && (caps?.enabled ? <AssistantSettings /> : <Card>
              <p className="text-sm text-muted">{tr(capsError ? "settings.loadFailed" : caps === null ? "settings.loading" : "settings.disabled")}</p>
              {capsError && <div className="mt-3 flex items-center gap-2"><button type="button" className="text-sm text-accent" onClick={() => void refreshCapabilities()}>{tr("settings.retry")}</button><Hint label={tr("settings.help").replace("{label}", tr("settings.retry"))} text={tr("settings.retry.hint")} align="end" /></div>}
            </Card>)}
          </>}
        </section>
      </div>
    </div>
  </div>;
}

export default function SettingsPage() {
  return <Suspense fallback={<PageSkeleton />}><SettingsContent /></Suspense>;
}
