"use client";

import Link from "next/link";
import { ArrowUpRight, Images, Shapes } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "./strings";

export default function ToolsPage() {
  const lang = useUIStore(s => s.lang);
  const tr = makePageT(lang, STRINGS);
  return <div className="h-full overflow-y-auto p-6 page-in" data-testid="tools-hub">
    <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
      <header><h1 className="font-serif text-xl font-semibold text-fg">{tr("title")}</h1>
        <p className="mt-0.5 max-w-2xl text-xs text-muted">{tr("intro")}</p></header>
      <Link href="/tools/illustration" className="group block max-w-3xl rounded-[12px] border border-border bg-surface p-5 transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-accent" data-testid="illustration-tool-entry">
        <div className="flex items-start justify-between gap-4"><span className="rounded-[10px] bg-accent-soft p-2.5 text-accent-strong"><Images size={22} aria-hidden="true" /></span><ArrowUpRight size={18} className="text-muted group-hover:text-accent" aria-hidden="true" /></div>
        <h2 className="mt-4 text-base font-semibold text-fg">{tr("illustration")}</h2><p className="mt-1.5 text-sm leading-6 text-muted">{tr("illustrationIntro")}</p>
        <div className="mt-4 flex flex-wrap gap-2">{(["v1", "v2", "v3"] as const).map(mode => <span key={mode} className="rounded-full border border-border px-3 py-1 text-xs text-fg-secondary">{mode.toUpperCase()} · {tr(mode)}</span>)}</div>
        <p className="mt-4 text-sm font-medium text-accent-strong">{tr("open")} →</p>
      </Link>
      <Link href="/diagram-library" className="flex max-w-3xl items-center gap-3 rounded-[12px] border border-border bg-surface p-4 hover:border-accent focus-visible:outline-2 focus-visible:outline-accent">
        <Shapes size={20} className="shrink-0 text-muted" aria-hidden="true" /><span className="min-w-0 flex-1"><span className="block text-sm font-medium text-fg">{tr("library")}</span><span className="mt-0.5 block text-xs leading-5 text-muted">{tr("libraryIntro")}</span></span><ArrowUpRight size={16} aria-hidden="true" />
      </Link>
    </div>
  </div>;
}
