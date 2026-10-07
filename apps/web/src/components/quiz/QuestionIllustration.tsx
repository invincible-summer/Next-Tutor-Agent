"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Image from "next/image";
import { Expand, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/assessment/strings";
import type { QuestionIllustrationData } from "@/lib/types";
import { getFrozenIllustration, illustrationFailure, illustrationStage, type IllustrationJob, type VisualRole } from "@/lib/api-illustrations";
import { DEMO_MODE } from "@/lib/demo";
import { useAuthStore } from "@/lib/auth-store";
import { apiAssetUrl } from "@/lib/api";

function supported(value: QuestionIllustrationData): boolean {
  if (value.kind === "raster") {
    return ((typeof value.data_url === "string" && value.data_url.startsWith("data:image/")) ||
      (typeof value.asset_url === "string" && value.asset_url.startsWith("/"))) &&
      typeof value.alt === "string" && value.alt.trim().length > 0;
  }
  const sanitizerVersion = Number(value.sanitizer_version);
  const components = (value.schema_version === 2 || value.schema_version === 3) && sanitizerVersion === 3;
  return value.kind === "svg" && (components || (value.schema_version === 1
    && (sanitizerVersion === 1 || sanitizerVersion === 2)))
    && typeof value.svg === "string" && value.svg.startsWith("<svg ")
    && new TextEncoder().encode(value.svg).length <= (components ? 128 : 24) * 1024
    && typeof value.content_hash === "string" && /^sha256:[a-f0-9]{64}$/.test(value.content_hash)
    && typeof value.alt === "string" && value.alt.trim().length > 0 && value.alt.length <= 600
    && typeof value.caption === "string" && value.caption.length <= 120
    && Number.isInteger(value.width) && value.width >= 320 && value.width <= 960
    && Number.isInteger(value.height) && value.height >= 200 && value.height <= 720
    && value.width / value.height >= 0.75 && value.width / value.height <= 3;
}

export function QuestionIllustration({ illustration, questionId, revision = 1, visualRole = "supplemental" }: {
  illustration?: QuestionIllustrationData | null;
  questionId?: string;
  revision?: number;
  visualRole?: VisualRole;
}) {
  const owner = useAuthStore(store => store.user?.id ?? "local");
  if (!illustration) return questionId && !DEMO_MODE
    ? <FrozenDiagram key={`${owner}:${questionId}:${revision}`} questionId={questionId} revision={revision} visualRole={visualRole} /> : null;
  return illustration.kind === "raster"
    ? <RasterDiagram key={`${illustration.asset_url}:${illustration.alt}`} value={illustration} />
    : <Diagram key={`${illustration.content_hash}:${illustration.svg}`} value={illustration} />;
}

function RasterDiagram({ value }: { value: QuestionIllustrationData }) {
  const lang = useUIStore((store) => store.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const [open, setOpen] = useState(false);
  const src = value.data_url?.startsWith("data:image/") ? value.data_url : apiAssetUrl(value.asset_url ?? "");
  const picture = (expanded: boolean) => (
    <div className="mx-auto w-full" style={{ maxWidth: expanded ? 960 : 640 }}>
      <Image src={src} alt={value.alt} width={960} height={540} unoptimized className="block h-auto w-full bg-white object-contain" />
    </div>
  );
  return <figure data-testid="question-illustration" className="my-3 min-w-0 overflow-hidden rounded-lg border border-border-light bg-surface">
    <div className="bg-white p-2">{picture(false)}</div>
    <figcaption className="flex items-start justify-between gap-2 border-t border-border-light px-3 py-2">
      <p className="min-w-0 flex-1 text-xs leading-5 text-fg-secondary">{value.caption || tr("illustration.title")}</p>
      <Button type="button" size="sm" variant="outline" className="shrink-0" onClick={() => setOpen(true)}>{tr("illustration.expand")}</Button>
    </figcaption>
    {open && <DiagramDialog title={tr("illustration.title")} closeText={tr("illustration.close")} onClose={() => setOpen(false)}>{picture(true)}</DiagramDialog>}
  </figure>;
}

function FrozenDiagram({ questionId, revision, visualRole }: { questionId: string; revision: number; visualRole: VisualRole }) {
  const owner = useAuthStore(store => store.user?.id ?? "local");
  const english = useUIStore(store => store.lang === "en");
  const [result, setResult] = useState<IllustrationJob | null>(null);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const value = await getFrozenIllustration(questionId, revision);
        if (!active || (useAuthStore.getState().user?.id ?? "local") !== owner) return;
        setResult(value);
        if (value.status === "running" || value.status === "queued") timer = setTimeout(() => void load(), 1500);
      } catch {
        if (active) setResult({ status: "failed", question_id: questionId, question_revision: revision, code: "run_interrupted" });
      }
    }
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, [questionId, revision, owner]);
  if (!result || result.status === "not_required") return null;
  if (result.status === "ready" && result.illustration) return <Diagram value={result.illustration} />;
  const pending = result.status === "queued" || result.status === "running";
  return <div role="status" className="my-3 flex items-center gap-2 rounded-lg border border-border-light bg-surface-sunken px-3 py-2 text-xs text-muted">
    {pending && <LoaderCircle size={14} className="shrink-0 animate-spin" aria-hidden="true" />}
    <span>{pending ? illustrationStage(result.progress?.stage ?? "preparation", english)
      : illustrationFailure(result.failure?.code ?? result.code ?? "", english)}
      {visualRole === "essential" && (english ? ". Complete diagram material is required to answer." : "；题图完成前不能作答。")}</span>
  </div>;
}

function Diagram({ value }: { value: QuestionIllustrationData }) {
  const lang = useUIStore((store) => store.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const close = useCallback(() => setOpen(false), []);
  const valid = supported(value);
  const src = useMemo(() => valid
    ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(value.svg)}` : "", [valid, value.svg]);

  if (!valid || failed) return (
    <div role="status" className="my-3 min-w-0 rounded-lg border border-border p-3 text-xs leading-5 text-muted">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p>{tr("illustration.unavailable")}</p>
        {valid && <Button type="button" size="sm" variant="outline" data-testid="question-illustration-retry"
          onClick={() => { setAttempt((previous) => previous + 1); setFailed(false); }}>
          {tr("illustration.retry")}
        </Button>}
      </div>
      {typeof value.alt === "string" && <p className="mt-2 whitespace-pre-wrap break-words">{value.alt.slice(0, 600)}</p>}
    </div>
  );

  const previewWidth = value.schema_version === 3 ? Math.min(value.width, 640)
    : Math.min(value.width, 640, 280 * value.width / value.height);
  const picture = (expanded: boolean) => (
    <div className="mx-auto w-full" style={{ maxWidth: expanded ? value.width : previewWidth }}>
      <Image key={`${expanded}:${attempt}`} unoptimized data-testid="question-illustration-image"
        src={src} alt={value.alt} width={value.width} height={value.height}
        loading="eager" decoding="async" onError={() => { setFailed(true); setOpen(false); }}
        className={`block h-auto w-full bg-white ${value.schema_version === 1 ? "dark:invert" : ""}`} />
    </div>
  );

  return (
    <figure data-testid="question-illustration" className="my-3 min-w-0 overflow-hidden rounded-lg border border-border-light bg-surface">
      <div className={`bg-white p-2 ${value.schema_version === 1 ? "dark:bg-black" : ""}`}>{picture(false)}</div>
      <figcaption className="border-t border-border-light px-3 py-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <p className="min-w-0 flex-1 break-words text-xs leading-5 text-fg-secondary">{value.caption || tr("illustration.title")}</p>
          <Button type="button" size="sm" variant="outline" className="shrink-0 whitespace-nowrap"
            data-testid="question-illustration-expand" icon={<Expand size={13} aria-hidden="true" />}
            onClick={() => setOpen(true)}>{tr("illustration.expand")}</Button>
        </div>
        <details className="mt-1 text-xs leading-5 text-muted">
          <summary className="cursor-pointer">{lang === "en" ? "Diagram description" : "图示说明"}</summary>
          <p className="mt-1 whitespace-pre-wrap break-words">{value.alt}</p>
        </details>
      </figcaption>
      {open && <DiagramDialog title={tr("illustration.title")} closeText={tr("illustration.close")} onClose={close}>
        {picture(true)}
        <p className="mt-3 whitespace-pre-wrap break-words text-xs leading-5">{value.caption || value.alt}</p>
      </DiagramDialog>}
    </figure>
  );
}

function DiagramDialog({ title, closeText, onClose, children }: {
  title: string;
  closeText: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        onClose();
      } else if (event.key === "Tab") {
        event.preventDefault();
        dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    };
    window.addEventListener("keydown", handleKey, true);
    return () => {
      window.removeEventListener("keydown", handleKey, true);
      if (previous?.isConnected) previous.focus();
    };
  }, [onClose]);

  return createPortal(
    <Modal open width={1000} onClose={onClose} ariaLabel={title}>
      <div ref={dialog}>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 id={titleId} className="min-w-0 font-medium">{title}</h2>
          <Button type="button" size="sm" variant="outline" className="shrink-0" onClick={onClose}>{closeText}</Button>
        </div>
        {children}
      </div>
    </Modal>, document.body,
  );
}
