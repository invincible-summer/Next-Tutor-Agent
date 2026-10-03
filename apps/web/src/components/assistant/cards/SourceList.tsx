"use client";

// 来源列表（A06 视觉 / A11 locator 跳转）。
// 点击来源经 routes 白名单解析 locator；目标页负责加载后定位与
// 不存在时的温和提示；不可用来源只展示不跳转。
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, ExternalLink, FileText } from "lucide-react";
import { useAssistantStore } from "@/lib/assistant/store";
import { resolveTargetUrl } from "@/lib/assistant/routes";
import { stringsFor } from "../strings";
import type { AssistantSource } from "@/lib/assistant/types.generated";

export function SourceList({ sources }: { sources: AssistantSource[] }) {
  const lang = useAssistantStore((s) => s.lang);
  const t = stringsFor(lang);
  const router = useRouter();
  const [open, setOpen] = useState(false);
  if (!sources.length) return null;

  const jump = (source: AssistantSource) => {
    const url = resolveTargetUrl(source.locator);
    if (url) router.push(url);
  };

  return (
    <div className="assistant-sources">
      <button
        type="button"
        className="assistant-sources-toggle"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <FileText size={13} aria-hidden />
        {t.viewSources(sources.length)}
        <ChevronDown size={13} aria-hidden
          className={open ? "rotate-180 transition-transform" : "transition-transform"} />
      </button>
      {open && (
        <ul className="assistant-sources-list">
          {sources.map((source) => {
            const url = resolveTargetUrl(source.locator);
            const clickable = source.availability === "available" && !!url;
            return (
              <li key={source.source_id} className="assistant-source-item"
                data-availability={source.availability}>
                <button
                  type="button"
                  className="assistant-source-title"
                  disabled={!clickable}
                  onClick={() => clickable && jump(source)}
                >
                  {source.availability !== "available"
                    ? lang === "zh" ? "来源已不可用" : "Source unavailable"
                    : source.title}
                  {clickable && <ExternalLink size={11} aria-hidden />}
                </button>
                {source.observed_at && (
                  <span className="assistant-source-date">
                    {source.observed_at.slice(0, 10)}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
