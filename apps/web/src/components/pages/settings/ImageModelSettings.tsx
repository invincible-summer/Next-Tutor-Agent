"use client";

import { useEffect, useState } from "react";
import { CircleAlert, ServerCog, ShieldCheck, Sparkles } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/settings/strings";
import { getImageCapability, type ImageCapability } from "@/lib/api-image-generation";

export function ImageModelSettings() {
  const lang = useUIStore((s) => s.lang);
  const tr = makePageT(lang, STRINGS);
  const [capability, setCapability] = useState<ImageCapability | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getImageCapability(controller.signal).then(setCapability).catch(() => setFailed(true));
    return () => controller.abort();
  }, []);
  return <Card className="space-y-5" data-testid="image-model-settings">
    <div className="flex items-start gap-3"><span className="rounded-lg bg-accent2-soft p-2 text-accent2-strong"><Sparkles size={18} /></span><div><h3 className="text-sm font-semibold text-fg">{tr("settings.image.title")}</h3><p className="mt-1 text-xs leading-5 text-muted">{tr("settings.image.desc")}</p></div></div>
    {failed ? <p className="flex items-center gap-2 text-sm text-warning"><CircleAlert size={16} />{tr("settings.image.statusFailed")}</p> : <div className="rounded-lg border border-border-light bg-bg p-4">
      <div className="flex items-center justify-between gap-3"><p className="flex items-center gap-2 text-xs font-medium text-fg-secondary"><ServerCog size={14} />{tr("settings.image.serverTitle")}</p><span className={`rounded-full px-2 py-1 text-[10px] ${capability?.configured ? "bg-success/10 text-success" : "bg-warning/10 text-warning"}`}>{capability ? (capability.configured ? tr("settings.image.configured") : tr("settings.image.unconfigured")) : tr("settings.image.loading")}</span></div>
      {capability && <dl className="mt-4 grid grid-cols-1 gap-3 text-xs sm:grid-cols-2"><div><dt className="text-muted">{tr("settings.image.provider")}</dt><dd className="mt-1 font-medium text-fg">{capability.provider}</dd></div><div><dt className="text-muted">{tr("settings.image.model")}</dt><dd className="mt-1 font-medium text-fg">{capability.model || "—"}</dd></div><div><dt className="text-muted">{tr("settings.image.protocol")}</dt><dd className="mt-1 text-fg-secondary">{capability.protocol}</dd></div><div><dt className="text-muted">{tr("settings.image.reference")}</dt><dd className="mt-1 text-fg-secondary">{capability.supports_reference ? tr("settings.image.referenceReady") : tr("settings.image.referenceUnavailable")}</dd></div></dl>}
    </div>}
    <p className="flex items-center gap-2 text-xs leading-5 text-muted"><ShieldCheck size={14} className="text-success" />{tr("settings.image.serverDesc")}</p>
  </Card>;
}
