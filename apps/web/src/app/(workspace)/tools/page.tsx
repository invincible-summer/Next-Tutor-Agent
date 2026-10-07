"use client";

import Link from "next/link";
import { useMemo } from "react";
import type { ReactNode } from "react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { ExportMark, PaperCompilerMark, SceneWeaveMark } from "@/components/pages/tools/ToolMarks";
import { STRINGS } from "./strings";

const MODES = ["v1", "v2", "v3", "v4"] as const;

function ToolEntry({
  href,
  testId,
  mark: Mark,
  title,
  description,
  action,
  children,
  tone = "teal",
}: {
  href: string;
  testId: string;
  mark: typeof SceneWeaveMark;
  title: string;
  description: string;
  action: string;
  children: ReactNode;
  tone?: "teal" | "terracotta";
}) {
  return (
    <Link
      href={href}
      className="group relative flex min-h-[300px] flex-col overflow-hidden rounded-[16px] border border-border bg-surface p-6 transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-accent/60 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 motion-reduce:transform-none"
      data-testid={testId}
    >
      <div className="flex items-start justify-between gap-4">
        <span className={`flex h-14 w-14 items-center justify-center rounded-[14px] ${tone === "terracotta" ? "bg-accent2-soft text-accent2-strong" : "bg-accent-soft text-accent-strong"}`}>
          <Mark className="h-9 w-9" />
        </span>
        <span className="flex items-center gap-1.5 pt-1 text-xs font-medium text-muted transition-colors group-hover:text-accent-strong">{action}<ExportMark className="h-4 w-4" /></span>
      </div>
      <div className="mt-8 max-w-[34rem]">
        <h2 className="font-serif text-[1.45rem] font-semibold tracking-tight text-fg">{title}</h2>
        <p className="mt-3 max-w-[40ch] text-sm leading-7 text-fg-secondary">{description}</p>
      </div>
      <div className="mt-auto pt-7">{children}</div>
    </Link>
  );
}

export default function ToolsPage() {
  const lang = useUIStore(s => s.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  return (
    <div className="h-full overflow-y-auto bg-bg p-5 page-in sm:p-7" data-testid="tools-hub">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-8">
        <header className="max-w-2xl">
          <h1 className="font-serif text-2xl font-semibold tracking-tight text-fg sm:text-[1.75rem]">{tr("title")}</h1>
          <p className="mt-2 text-sm leading-7 text-fg-secondary">{tr("intro")}</p>
        </header>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <ToolEntry
            href="/tools/illustration"
            testId="illustration-tool-entry"
            mark={SceneWeaveMark}
            title={tr("illustration")}
            description={tr("illustrationIntro")}
            action={tr("open")}
          >
            <div className="flex flex-wrap gap-2" aria-label={tr("mode")}>
              {MODES.map(mode => (
                <span key={mode} className="rounded-full border border-border-light bg-bg px-3 py-1.5 text-xs text-fg-secondary">
                  <span className="font-medium text-fg">{mode.toUpperCase()}</span> · {tr(mode)}
                </span>
              ))}
            </div>
            <p className="mt-4 text-xs leading-5 text-muted">{tr("illustrationHint")}</p>
          </ToolEntry>

          <ToolEntry
            href="/tools/worksheet"
            testId="worksheet-tool-entry"
            mark={PaperCompilerMark}
            title={tr("worksheet")}
            description={tr("worksheetIntro")}
            action={tr("openWorksheet")}
            tone="terracotta"
          >
            <div className="grid grid-cols-3 gap-2">
              {[tr("worksheetStepSetup"), tr("worksheetStepGenerate"), tr("worksheetStepExport")].map((step, index) => (
                <div key={step} className="rounded-[10px] border border-border-light bg-bg px-3 py-2.5">
                  <span className="block text-[11px] font-semibold text-accent2-strong">{String(index + 1).padStart(2, "0")}</span>
                  <span className="mt-1 block text-xs leading-5 text-fg-secondary">{step}</span>
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs leading-5 text-muted">{tr("worksheetHint")}</p>
          </ToolEntry>
        </div>
      </div>
    </div>
  );
}
