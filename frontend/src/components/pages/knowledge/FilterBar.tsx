"use client";
// 知识谱系分层筛选栏：行 1 学段单选 segmented tabs（同屏只展示一个学段），
// 右侧挂搜索槽位；行 2 学科 / 教材组 / 卷 三级下拉 + 清除筛选 + 前置链开关。
// 全部状态由页面持有，本组件只负责呈现与回调。
import { GitFork, X } from "lucide-react";
import { cn } from "@/lib/cn";
import type { KnowledgeTaxonomyGroup, KnowledgeTaxonomyVolume } from "@/lib/types-modules";
import type { ReactNode } from "react";

type Tr = (key: string, fallback?: string) => string;

const SELECT =
  "h-7 max-w-52 cursor-pointer truncate rounded-[7px] border border-border bg-surface px-1.5 text-xs text-fg outline-none transition-colors focus:border-accent";

export interface FilterLevelInfo {
  name: string;
  groupCount: number;
}

export function FilterBar({
  levels,
  level,
  onPickLevel,
  subjects,
  subject,
  onPickSubject,
  groups,
  textbookId,
  onPickTextbook,
  volumes,
  fileId,
  onPickFile,
  hasScopeFilters,
  onClearFilters,
  showPrereq,
  prereqOnly,
  onTogglePrereq,
  right,
  tr,
}: {
  levels: FilterLevelInfo[];
  level: string | null;
  onPickLevel: (lv: string) => void;
  subjects: string[];
  subject: string | null;
  onPickSubject: (s: string | null) => void;
  groups: KnowledgeTaxonomyGroup[];
  textbookId: string | null;
  onPickTextbook: (id: string | null) => void;
  /** 选中教材组的卷列表；空数组时不渲染卷下拉 */
  volumes: KnowledgeTaxonomyVolume[];
  fileId: string | null;
  onPickFile: (id: string | null) => void;
  hasScopeFilters: boolean;
  onClearFilters: () => void;
  showPrereq: boolean;
  prereqOnly: boolean;
  onTogglePrereq: () => void;
  /** 行 1 右侧槽位（搜索框） */
  right?: ReactNode;
  tr: Tr;
}) {
  return (
    <>
      {/* 行 1：学段 segmented 单选（一次只看一个学段） */}
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <div className="inline-flex shrink-0 items-center gap-0.5 rounded-[9px] border border-border bg-surface p-0.5">
          {levels.map((lv) => {
            const active = level === lv.name;
            return (
              <button
                key={lv.name}
                onClick={() => onPickLevel(lv.name)}
                className={cn(
                  "flex h-7 cursor-pointer items-center gap-1.5 rounded-[7px] px-3 text-xs font-medium transition-colors",
                  active ? "bg-accent text-white" : "text-fg-secondary hover:bg-surface-hover hover:text-accent",
                )}
              >
                {tr(`level.${lv.name}`, lv.name)}
                <span
                  className={cn(
                    "tnum rounded-full px-1.5 py-px text-[10px] leading-none",
                    active ? "bg-white/20 text-white" : "bg-surface-sunken text-muted",
                  )}
                >
                  {lv.groupCount}
                </span>
              </button>
            );
          })}
        </div>
        {right}
      </div>

      {/* 行 2：学科 / 教材组 / 卷 下拉 + 清除筛选 + 前置链开关 */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2">
        <label className="flex shrink-0 items-center gap-1.5">
          <span className="text-xs text-muted">{tr("filterSubject")}</span>
          {/* 学科必选（单科显示）：不提供「全部」选项 */}
          <select
            value={subject ?? ""}
            onChange={(e) => onPickSubject(e.target.value || null)}
            className={SELECT}
          >
            {subjects.length === 0 && <option value="">{tr("filterAny")}</option>}
            {subjects.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>

        {groups.length > 0 && (
          <label className="flex shrink-0 items-center gap-1.5">
            <span className="text-xs text-muted">{tr("filterTextbook")}</span>
            <select
              value={textbookId ?? ""}
              onChange={(e) => onPickTextbook(e.target.value || null)}
              className={SELECT}
            >
              <option value="">{tr("filterAny")}</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id} title={g.note || g.name}>
                  {g.name}（{g.node_count}）
                </option>
              ))}
            </select>
          </label>
        )}

        {volumes.length > 0 && (
          <label className="flex shrink-0 items-center gap-1.5">
            <span className="text-xs text-muted">{tr("filterVolume")}</span>
            <select
              value={fileId ?? ""}
              onChange={(e) => onPickFile(e.target.value || null)}
              className={SELECT}
            >
              <option value="">{tr("filterAny")}</option>
              {volumes.map((v) => (
                <option
                  key={v.file_id}
                  value={v.file_id}
                  title={`${v.chapter_count} 章${v.section_count ? ` · ${v.section_count} 节` : ""} · ${v.concept_count} 概念${v.error ? ` · ${v.error}` : ""}`}
                >
                  {v.name}
                  {v.status === "failed" ? " ⚠" : v.truncated ? " …" : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        {hasScopeFilters && (
          <button
            onClick={onClearFilters}
            className="flex h-7 cursor-pointer items-center gap-1 rounded-[7px] border border-border bg-surface px-2 text-xs text-fg-secondary transition-colors hover:border-accent hover:text-accent"
          >
            <X size={12} />
            {tr("clearFilters")}
          </button>
        )}

        {showPrereq && (
          <button
            onClick={onTogglePrereq}
            className={cn(
              "flex h-7 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-xs transition-colors",
              prereqOnly
                ? "border-accent bg-accent-soft text-accent-strong"
                : "border-border bg-surface text-fg-secondary hover:border-accent hover:text-accent",
            )}
          >
            <GitFork size={12} />
            {tr("prereqOnly")}
          </button>
        )}
      </div>
    </>
  );
}
