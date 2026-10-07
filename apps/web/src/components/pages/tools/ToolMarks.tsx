import type { ReactNode, SVGProps } from "react";

type MarkProps = SVGProps<SVGSVGElement> & { title?: string };

/**
 * Product-specific marks for the tool surfaces. These are deliberately drawn
 * in the repo instead of borrowing the usual image/file/sparkle glyphs: the
 * marks describe the work students do here, not the generic object category.
 */
function Mark({ children, title, ...props }: MarkProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      {...props}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

export function SceneWeaveMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="M10 17.5 20 7.5l11 11-10 10-11-11Z" />
      <path d="m33 35 10-10 11 11-10 10-11-11Z" />
      <path d="M20 18.5h18M27 25.5l10 10" />
      <circle cx="20" cy="18.5" r="3.2" fill="currentColor" stroke="none" />
      <circle cx="43" cy="36" r="3.2" fill="currentColor" stroke="none" />
      <path d="M9 48h46" opacity=".38" />
    </Mark>
  );
}

export function PaperCompilerMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="M15 8.5h26l8 8v39H15z" />
      <path d="M41 8.5v9h8" />
      <path d="M23 28h17M23 35h17M23 42h11" />
      <circle cx="19" cy="28" r="1.8" fill="currentColor" stroke="none" />
      <circle cx="19" cy="35" r="1.8" fill="currentColor" stroke="none" />
      <circle cx="19" cy="42" r="1.8" fill="currentColor" stroke="none" />
      <path d="m38 49 4 4 8-9" />
    </Mark>
  );
}

export function MaterialAtlasMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <rect x="9" y="12" width="46" height="40" rx="5" />
      <path d="M16 44 25 30l8 7 6-8 9 15" />
      <circle cx="23" cy="22" r="3" />
      <path d="M16 53h32" opacity=".42" />
    </Mark>
  );
}

export function ComposeMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="M13 18h38v28H13z" />
      <path d="M21 26h22M21 33h14M21 40h8" />
      <path d="M45 47v8M41 51h8" />
    </Mark>
  );
}

export function ExportMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="M15 10h34v44H15z" />
      <path d="M23 22h18M23 29h18M23 36h11" />
      <path d="M42 45v10M37 50l5 5 5-5" />
    </Mark>
  );
}

export function RevisionMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="M17 20a19 19 0 1 1-2 18" />
      <path d="M17 12v8h-8" />
      <circle cx="32" cy="32" r="7" />
      <path d="M32 28v5l3 2" />
    </Mark>
  );
}

export function BackMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="M49 15H27l-12 17 12 17h22" />
      <path d="M16 32h31M29 24l-8 8 8 8" />
      <path d="M49 25v14" opacity=".45" />
    </Mark>
  );
}

/** Small utility marks for tool controls. They share the same line language
 * as the product marks above, so a search/close/busy state never falls back
 * to an unrelated icon library glyph. */
export function SearchMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <circle cx="28" cy="28" r="14" />
      <path d="m39 39 13 13" />
      <path d="M22 28h12M28 22v12" opacity=".5" />
    </Mark>
  );
}

export function CloseMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="M18 18 46 46M46 18 18 46" />
      <circle cx="32" cy="32" r="20" opacity=".35" />
    </Mark>
  );
}

export function ArchiveMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="M14 18h36v34H14z" />
      <path d="M11 12h42v8H11z" />
      <path d="M25 29h14M27 36h10" />
      <path d="M20 52h24" opacity=".45" />
    </Mark>
  );
}

export function BusyMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="M32 10a22 22 0 1 1-15.6 6.4" />
      <path d="M16.4 16.4v9h9" />
      <circle cx="32" cy="32" r="3" fill="currentColor" stroke="none" />
    </Mark>
  );
}

export function AlertMark(props: MarkProps) {
  return (
    <Mark title={props.title} {...props}>
      <path d="m32 10 23 42H9L32 10Z" />
      <path d="M32 25v12M32 43v1" />
    </Mark>
  );
}
