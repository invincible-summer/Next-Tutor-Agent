"use client";
import type { SlideSpec } from "@/lib/types-classroom.generated";
import { cardPaletteFor } from "./theme-palette";

/** Content-based miniature; the live, sandboxed slide remains the main preview. */
export function SlideThumbnail({ slide, themeId }: { slide: SlideSpec; themeId?: string }) {
  const palette = cardPaletteFor(themeId);
  return (
    <span className="slide-thumbnail" aria-hidden="true" style={{ background: palette.bg, color: palette.text }}>
      <span className="absolute left-3 top-3 h-0.5 w-5" style={{ background: palette.accent }} />
      <span className="relative z-10 block line-clamp-2 text-[11px] font-semibold leading-[1.5]">{slide.title}</span>
      <span className="mt-2 block line-clamp-2 text-[7px] leading-relaxed opacity-55">{slide.segments[0]?.display_text}</span>
      <span className="absolute bottom-2 right-3 text-[8px] opacity-40">{String(slide.order).padStart(2, "0")}</span>
    </span>
  );
}
