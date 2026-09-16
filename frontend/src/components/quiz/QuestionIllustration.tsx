"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Expand } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/assessment/strings";
import type { QuestionIllustrationData } from "@/lib/types";

function supported(value: QuestionIllustrationData): boolean {
  return value.kind === "svg" && value.schema_version === 1 && value.sanitizer_version === 1
    && typeof value.svg === "string" && value.svg.startsWith("<svg ")
    && new TextEncoder().encode(value.svg).length <= 24 * 1024
    && /^sha256:[a-f0-9]{64}$/.test(value.content_hash)
    && typeof value.alt === "string" && !!value.alt.trim() && value.alt.length <= 600
    && typeof value.caption === "string" && value.caption.length <= 120
    && Number.isInteger(value.width) && value.width >= 320 && value.width <= 960
    && Number.isInteger(value.height) && value.height >= 200 && value.height <= 720;
}

/** Only canonical server diagrams. SVG stays in the isolated image context. */
export function QuestionIllustration({ illustration }: {
  illustration?: QuestionIllustrationData | null;
}) {
  if (!illustration) return null;
  return <IllustrationImage key={illustration.content_hash || "unsupported"} value={illustration} />;
}

function IllustrationImage({ value }: { value: QuestionIllustrationData }) {
  const lang = useUIStore((s) => s.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  const valid = supported(value);
  const src = useMemo(() => valid ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(value.svg)}` : "", [valid, value.svg]);
  if (!valid || failed) {
    return <div role="status" className="my-3 rounded-lg border border-border p-3 text-sm text-muted">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p>{tr("illustration.unavailable")}</p>
        {valid && failed && (
          <Button
            type="button"
            data-testid="question-illustration-retry"
            size="sm"
            variant="outline"
            onClick={() => setFailed(false)}
          >
            {tr("illustration.retry")}
          </Button>
        )}
      </div>
      {typeof value.alt === "string" && <p className="mt-1 whitespace-pre-wrap">{value.alt.slice(0, 600)}</p>}
    </div>;
  }
  // Native img deliberately preserves SVG's restricted image context.
  const picture = (expanded = false) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img data-testid="question-illustration-image" src={src} alt={value.alt}
      width={value.width} height={value.height}
      loading="eager" decoding="async" onError={() => setFailed(true)}
      className="mx-auto block h-auto w-full bg-white dark:invert"
      style={{ maxWidth: expanded ? value.width : Math.min(value.width, 720) }} />
  );
  return (
    <figure data-testid="question-illustration" className="my-3 min-w-0 overflow-hidden rounded-lg border border-border-light bg-white p-2 dark:bg-black">
      {picture()}
      <figcaption className="mt-1 flex items-center justify-between gap-2 text-xs text-muted">
        <span className="whitespace-pre-wrap">{value.caption || value.alt}</span>
        <Button
          type="button"
          data-testid="question-illustration-expand"
          size="sm"
          variant="outline"
          icon={<Expand size={13} />}
          className="shrink-0"
          onClick={() => setOpen(true)}
        >
          {tr("illustration.expand")}
        </Button>
      </figcaption>
      {open && <ExpandedIllustration title={tr("illustration.title")} closeText={tr("illustration.close")}
        onClose={() => setOpen(false)}>{picture(true)}{value.caption && <p className="mt-2">{value.caption}</p>}</ExpandedIllustration>}
    </figure>
  );
}

function ExpandedIllustration({ title, closeText, onClose, children }: {
  title: string; closeText: string; onClose: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        onClose();
      } else if (event.key === "Tab") {
        // The close button is this read-only viewer's only interactive child.
        event.preventDefault();
        dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    };
    window.addEventListener("keydown", keys, true);
    return () => { window.removeEventListener("keydown", keys, true); previous?.focus(); };
  }, [onClose]);
  return createPortal(<Modal open onClose={onClose} width={1000}>
    <div ref={dialog} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="font-medium">{title}</span><Button size="sm" onClick={onClose}>{closeText}</Button>
      </div>
      {children}
    </div>
  </Modal>, document.body);
}
