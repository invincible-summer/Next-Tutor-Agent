"use client";
// 教材库边栏：学段 → 学科 → 教材组 → 卷 四层导航（与知识谱系同一分层投影）。
// 数据源 GET /knowledge/taxonomy（M5 唯一事实源，教材组自带卷/状态/章节数）：
// 选中教材组过滤主区卡片网格；点击卷打开该组详情抽屉（复用 TextbookDrawer）。
import { useMemo, useState } from "react";
import { BookOpen, BookText, ChevronRight, CircleAlert, GraduationCap } from "lucide-react";
import { cn } from "@/lib/cn";
import { KNOWLEDGE_LEVEL_ORDER } from "@/lib/labels";
import { Badge } from "@/components/ui/Badge";
import type {
  KnowledgeTaxonomyLevel,
  KnowledgeTaxonomyVolume,
} from "@/lib/types-modules";

function GroupLabel({ children }: { children: string }) {
  return (
    <div className="px-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted/70">{children}</div>
  );
}

/** 教材组聚合状态：任一卷 failed ⚠；任一卷构建中 ⋯（悬停见卷级明细）。 */
function groupMarkers(volumes: KnowledgeTaxonomyVolume[]) {
  const failed = volumes.some((v) => v.status === "failed");
  const building = volumes.some((v) => v.status === "building" || v.status === "ocr_waiting");
  return { failed, building };
}

export function TextbookSidebar({
  tr,
  levels: rawLevels,
  loading,
  selectedGroupId,
  onSelectGroup,
  onOpenTextbook,
}: {
  tr: (key: string, fallback?: string) => string;
  levels: KnowledgeTaxonomyLevel[];
  loading: boolean;
  selectedGroupId: string | null;
  onSelectGroup: (id: string | null) => void;
  onOpenTextbook: (id: string) => void;
}) {
  const levels = useMemo(() => {
    const known = KNOWLEDGE_LEVEL_ORDER.filter((lv) =>
      rawLevels.some((x) => x.name === lv && x.subjects.some((s) => s.groups.length > 0)));
    const extra = rawLevels
      .filter((x) => !KNOWLEDGE_LEVEL_ORDER.includes(x.name) && x.subjects.some((s) => s.groups.length > 0))
      .map((x) => x.name)
      .sort();
    const byName = new Map(rawLevels.map((x) => [x.name, x]));
    return [...known, ...extra]
      .map((name) => byName.get(name)!)
      .map((lv) => ({
        ...lv,
        subjects: lv.subjects.filter((s) => s.groups.length > 0),
      }));
  }, [rawLevels]);

  const totalGroups = useMemo(
    () => levels.reduce((n, lv) => n + lv.subjects.reduce((m, s) => m + s.groups.length, 0), 0),
    [levels],
  );

  // 折叠状态：key = 学段名 / `学段/学科` / `学段/学科/组id`（卷层）。
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(levels[0] ? [levels[0].name] : []));
  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const rowCls = (active: boolean) =>
    cn(
      "group flex cursor-pointer items-center gap-2 rounded-[8px] px-2 py-1.5",
      active ? "bg-accent-soft" : "hover:bg-surface-hover",
    );
  const textCls = (active: boolean) =>
    cn("min-w-0 flex-1 truncate text-sm", active ? "font-medium text-accent-strong" : "text-fg");
  const chevronCls = (open: boolean) =>
    cn("shrink-0 text-muted transition-transform", open && "rotate-90");

  return (
    <aside className="flex w-72 shrink-0 flex-col overflow-y-auto border-r border-border bg-surface">
      <div className="flex flex-col gap-1 p-3">
        <GroupLabel>{tr("res.tb.title", "教材库")}</GroupLabel>

        <div onClick={() => onSelectGroup(null)} className={rowCls(selectedGroupId === null)}>
          <BookText size={14} className={cn("shrink-0", selectedGroupId === null ? "text-accent" : "text-muted")} />
          <span className={textCls(selectedGroupId === null)}>{tr("res.tb.nav.all", "全部教材")}</span>
          <Badge tone="muted" className="tnum">{totalGroups}</Badge>
        </div>

        {loading && levels.length === 0 && (
          <div className="px-1 py-1 text-xs text-muted">{tr("res.tb.nav.loading", "载入教材分层…")}</div>
        )}
        {!loading && levels.length === 0 && (
          <div className="px-1 py-1 text-xs text-muted">{tr("res.tb.nav.empty", "暂无教材，先在右侧上传")}</div>
        )}

        {levels.map((lv) => {
          const lvKey = lv.name;
          const lvOpen = expanded.has(lvKey);
          const groupCount = lv.subjects.reduce((m, s) => m + s.groups.length, 0);
          return (
            <div key={lvKey} className="flex flex-col gap-1">
              <div onClick={() => toggle(lvKey)} className={rowCls(false)}>
                <GraduationCap size={14} className="shrink-0 text-muted" />
                <span className={textCls(false)}>{lv.name}</span>
                <Badge tone="muted" className="tnum">{groupCount}</Badge>
                <ChevronRight size={13} className={chevronCls(lvOpen)} />
              </div>

              {lvOpen && lv.subjects.map((sub) => {
                const subKey = `${lvKey}/${sub.name}`;
                const subOpen = expanded.has(subKey);
                return (
                  <div key={subKey} className="flex flex-col gap-1">
                    <div
                      onClick={() => toggle(subKey)}
                      className={cn(rowCls(false), "ml-3 border-l border-border-light pl-2")}
                    >
                      <span className={textCls(false)}>{sub.name}</span>
                      <Badge tone="muted" className="tnum">{sub.groups.length}</Badge>
                      <ChevronRight size={13} className={chevronCls(subOpen)} />
                    </div>

                    {subOpen && sub.groups.map((g) => {
                      const gOpen = expanded.has(`${subKey}/${g.id}`);
                      const active = selectedGroupId === g.id;
                      const { failed, building } = groupMarkers(g.volumes);
                      return (
                        <div key={g.id} className="flex flex-col gap-1">
                          <div
                            onClick={() => onSelectGroup(active ? null : g.id)}
                            title={g.note || g.name}
                            className={cn(rowCls(active), "ml-6 border-l border-border-light pl-2")}
                          >
                            <BookOpen size={14} className={cn("shrink-0", active ? "text-accent" : "text-muted")} />
                            <span className={textCls(active)}>{g.name}</span>
                            {failed && <CircleAlert size={12} className="shrink-0 text-warning" />}
                            {building && (
                              <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent" />
                            )}
                            <button
                              onClick={(e) => { e.stopPropagation(); toggle(`${subKey}/${g.id}`); }}
                              title={tr("res.tb.group.volumes.title", "分卷")}
                              className="shrink-0 cursor-pointer rounded p-0.5 text-muted hover:bg-surface hover:text-fg"
                            >
                              <ChevronRight size={13} className={chevronCls(gOpen)} />
                            </button>
                          </div>

                          {gOpen && g.volumes.length > 0 && (
                            <div className="ml-9 flex flex-col gap-0.5 border-l border-border-light pl-2">
                              {g.volumes.map((v) => (
                                <button
                                  key={v.file_id}
                                  onClick={() => onOpenTextbook(g.id)}
                                  title={`${v.chapter_count} 章 · ${v.concept_count} 概念${v.error ? ` · ${v.error}` : ""}`}
                                  className="flex cursor-pointer items-center gap-1.5 rounded-[8px] px-2 py-1 text-left text-xs text-fg-secondary transition-colors hover:bg-surface-hover hover:text-fg"
                                >
                                  <span className="min-w-0 flex-1 truncate">
                                    {v.name}
                                    {v.status === "failed" ? " ⚠" : v.truncated ? " …" : ""}
                                  </span>
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
