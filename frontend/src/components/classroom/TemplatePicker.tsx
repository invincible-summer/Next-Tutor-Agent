"use client";
/* 5 个视觉模板的原创 CSS 缩略图（plan.md §4.1：静态原创、无外部请求）。
 * 缩略图配色取自 backend/app/classroom/render/themes.py 的真实 token，
 * 让用户在备课时预览到的就是最终课件的面貌。
 */
import { Check } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { cn } from "@/lib/cn";
import type { ThemeTemplateInfo } from "@/lib/types-classroom.generated";
import { STRINGS } from "./strings";

interface ThumbnailSpec {
  bg: string;
  surface: string;
  text: string;
  muted: string;
  accent: string;
  accentSoft: string;
  border: string;
  radius: string;
  /** 主题特有的装饰元素（纯 CSS，原创绘制）。 */
  decor: "section-bar" | "chalk-grid" | "big-image" | "grid-chart" | "soft-round";
}

const SPECS: Record<string, ThumbnailSpec> = {
  "academic_clear@1": {
    bg: "#FAF7F0", surface: "#FFFFFF", text: "#1B2A4A", muted: "#5A6B8C",
    accent: "#2456C6", accentSoft: "#E3EBFA", border: "#D8DCE6",
    radius: "5px", decor: "section-bar",
  },
  "chalk_focus@1": {
    bg: "#21372F", surface: "#2A443B", text: "#F5F1E6", muted: "#B9C4B4",
    accent: "#F2D478", accentSoft: "#3A5A4E", border: "#3C574C",
    radius: "3px", decor: "chalk-grid",
  },
  "visual_story@1": {
    bg: "#FFFFFF", surface: "#F7F5F1", text: "#22252A", muted: "#6B7280",
    accent: "#C2542D", accentSoft: "#F7E5DC", border: "#E5E0D8",
    radius: "7px", decor: "big-image",
  },
  "lab_notebook@1": {
    bg: "#FBFCF7", surface: "#FFFFFF", text: "#16324F", muted: "#5B7186",
    accent: "#0F766E", accentSoft: "#DDF0EE", border: "#CFDCD6",
    radius: "2px", decor: "grid-chart",
  },
  "gentle_beginner@1": {
    bg: "#FFF9F2", surface: "#FFFFFF", text: "#3D3A50", muted: "#7A7790",
    accent: "#7C6FD9", accentSoft: "#ECE9FB", border: "#E7E2F2",
    radius: "9px", decor: "soft-round",
  },
};

function Thumb({ spec }: { spec: ThumbnailSpec }) {
  return (
    <div
      className="relative h-[62px] w-full overflow-hidden"
      style={{ background: spec.bg, borderRadius: spec.radius }}
      aria-hidden="true"
    >
      {spec.decor === "chalk-grid" && (
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: "radial-gradient(rgba(245,241,230,.14) 1px, transparent 1px)",
            backgroundSize: "11px 11px",
          }}
        />
      )}
      {spec.decor === "grid-chart" && (
        <div
          className="absolute inset-0"
          style={{
            backgroundImage:
              "linear-gradient(rgba(15,118,110,.12) 1px, transparent 1px), linear-gradient(90deg, rgba(15,118,110,.12) 1px, transparent 1px)",
            backgroundSize: "13px 13px",
          }}
        />
      )}
      {spec.decor === "big-image" && (
        <div
          className="absolute right-1.5 top-1.5 h-[36px] w-[52px]"
          style={{
            background: `linear-gradient(135deg, ${spec.accent} 0%, #2D6A4F 100%)`,
            borderRadius: spec.radius,
          }}
        />
      )}
      <div className="relative flex h-full flex-col gap-1 p-1.5">
        <div className="flex items-center gap-1">
          {spec.decor === "section-bar" && (
            <span
              className="inline-block h-[9px] w-[3px]"
              style={{ background: spec.accent }}
            />
          )}
          <span
            className="h-[7px] rounded-[2px]"
            style={{
              background: spec.text,
              width: spec.decor === "big-image" ? "44%" : "58%",
              opacity: 0.92,
            }}
          />
          {spec.decor === "soft-round" && (
            <span
              className="ml-auto h-[8px] w-[20px] rounded-full"
              style={{ background: spec.accentSoft }}
            />
          )}
        </div>
        <div
          className="flex-1 rounded-[3px]"
          style={{ background: spec.surface, border: `1px solid ${spec.border}` }}
        >
          {spec.decor === "grid-chart" ? (
            <div className="flex h-full items-end gap-[3px] p-1">
              {[38, 62, 48, 80, 58].map((h, i) => (
                <span
                  key={i}
                  style={{
                    height: `${h}%`,
                    width: 5,
                    background: i === 3 ? spec.accent : spec.accentSoft,
                    borderTop: `1px solid ${spec.accent}`,
                  }}
                />
              ))}
            </div>
          ) : spec.decor === "chalk-grid" ? (
            <div className="flex h-full flex-col gap-[4px] p-1.5">
              <span className="h-[3px] w-[80%]" style={{ background: spec.accent, opacity: 0.85 }} />
              <span className="h-[2px] w-[70%]" style={{ background: spec.text, opacity: 0.5 }} />
              <span
                className="h-[2px] w-[75%]"
                style={{ background: spec.text, opacity: 0.5, borderBottom: `1px dashed ${spec.accent}` }}
              />
            </div>
          ) : (
            <div className="flex h-full flex-col justify-center gap-[4px] p-1.5">
              <span className="h-[3px] w-[86%] rounded-[2px]" style={{ background: spec.muted, opacity: 0.55 }} />
              <span className="h-[3px] w-[72%] rounded-[2px]" style={{ background: spec.muted, opacity: 0.55 }} />
              <span className="h-[3px] w-[50%] rounded-[2px]" style={{ background: spec.accent, opacity: 0.8 }} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function TemplatePicker({ themes, value, onChange }: {
  themes: ThemeTemplateInfo[];
  value: string;
  onChange: (themeId: string) => void;
}) {
  const { lang } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  return (
    <div
      role="radiogroup"
      aria-label={tr("cls.form.theme")}
      className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
    >
      {themes.map((theme) => {
        const spec = SPECS[theme.theme_id] ?? SPECS["academic_clear@1"];
        const selected = value === theme.theme_id;
        const name = lang === "en" ? theme.name_en : theme.name_zh;
        const desc = lang === "en" ? theme.description_en : theme.description_zh;
        return (
          <button
            key={theme.theme_id}
            type="button"
            role="radio"
            aria-checked={selected}
            title={desc}
            onClick={() => onChange(theme.theme_id)}
            className={cn(
              "cursor-pointer rounded-[10px] border p-1.5 text-left transition-all",
              selected
                ? "border-accent bg-accent-soft/30 shadow-[0_0_0_3px_rgb(var(--accent)/0.14)]"
                : "border-border hover:border-accent/50",
            )}
          >
            <div className="relative">
              <Thumb spec={spec} />
              {selected && (
                <span className="absolute -right-1 -top-1 flex h-4.5 w-4.5 items-center justify-center rounded-full bg-accent text-white shadow-sm">
                  <Check size={11} strokeWidth={3} />
                </span>
              )}
            </div>
            <p className={cn(
              "mt-1.5 flex items-center gap-1 truncate text-[0.72rem] font-medium",
              selected ? "text-accent-strong" : "text-fg-secondary",
            )}>
              {name}
            </p>
            <p className="line-clamp-2 text-[0.62rem] leading-snug text-muted/80">{desc}</p>
          </button>
        );
      })}
    </div>
  );
}
