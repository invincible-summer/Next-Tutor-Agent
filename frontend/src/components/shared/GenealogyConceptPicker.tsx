"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Search, X } from "lucide-react";
import { getKnowledgeGraph, getKnowledgeTaxonomy } from "@/lib/api-modules";
import type {
  KnowledgeEdge,
  KnowledgeNode,
  KnowledgeTaxonomyLevel,
} from "@/lib/types-modules";
import { useUIStore } from "@/lib/store";
import { cn } from "@/lib/cn";
import { AnchoredPopover } from "@/components/ui/AnchoredPopover";

/** 选择的概念（图谱 id + 展示名）。 */
export interface PickedConcept {
  id: string;
  name: string;
}

const STRINGS = {
  zh: {
    title: "从知识谱系选概念（可选，支持整章勾选）",
    level: "学段",
    subject: "学科",
    group: "教材",
    groupNone: "选择教材后展示章节",
    loading: "加载中…",
    loadFail: "谱系加载失败，可稍后重试或留空",
    searchPh: "搜索概念名…",
    chapters: "章",
    concepts: "个概念",
    selected: "已选 {n} 个概念",
    clear: "清空",
    empty: "该教材暂无图谱数据",
    overLimit: "概念较多，目标链将按依赖上限截断",
    expand: "展开章节",
  },
  en: {
    title: "Pick concepts from the genealogy (optional, whole chapters ok)",
    level: "Stage",
    subject: "Subject",
    group: "Textbook",
    groupNone: "Pick a textbook to see chapters",
    loading: "Loading…",
    loadFail: "Failed to load the genealogy — retry later or leave empty",
    searchPh: "Search concepts…",
    chapters: "chapters",
    concepts: "concepts",
    selected: "{n} concepts selected",
    clear: "Clear",
    empty: "No graph data for this textbook yet",
    overLimit: "Many concepts — the goal chain is capped by dependency limits",
    expand: "Expand chapter",
  },
} as const;

const SELECT =
  "h-7.5 min-w-0 flex-1 rounded-[8px] border border-border bg-surface px-2 text-xs text-fg outline-none focus:border-accent";

/** 谱系概念选择器：学段→学科→教材组→章节树勾选 + 概念搜索。
 *  编排 GoalForm 与测评 ConfigCard 共用；选择结果是图谱概念 id 列表。 */
export function GenealogyConceptPicker({
  selected,
  onChange,
}: {
  selected: PickedConcept[];
  onChange: (next: PickedConcept[]) => void;
}) {
  const lang = useUIStore((s) => s.lang);
  const t = (k: keyof typeof STRINGS.zh, n?: number) =>
    (STRINGS[lang === "en" ? "en" : "zh"] as Record<string, string>)[k].replace(
      "{n}",
      String(n ?? 0),
    );

  const [levels, setLevels] = useState<KnowledgeTaxonomyLevel[]>([]);
  const [level, setLevel] = useState("");
  const [subject, setSubject] = useState("");
  const [groupId, setGroupId] = useState("");
  const [nodes, setNodes] = useState<KnowledgeNode[]>([]);
  const [edges, setEdges] = useState<KnowledgeEdge[]>([]);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [graphError, setGraphError] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // 搜索命中面板的外点关闭标记：记录被关掉时的搜索词，输入变化即重新弹出
  const [hitsDismissedFor, setHitsDismissedFor] = useState("");
  const searchWrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    getKnowledgeTaxonomy()
      .then((r) => {
        if (!alive || r.status !== "ok" || !r.levels?.length) return;
        setLevels(r.levels);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const subjectNames = useMemo(() => {
    const lv = levels.find((l) => l.name === level);
    return lv ? lv.subjects.map((s) => s.name) : [];
  }, [levels, level]);

  const groups = useMemo(() => {
    const lv = levels.find((l) => l.name === level);
    const sub = lv?.subjects.find((s) => s.name === subject);
    return sub?.groups ?? [];
  }, [levels, level, subject]);

  const loadGraph = useCallback(
    (gid: string) => {
      const g = groups.find((x) => x.id === gid);
      if (!g) return;
      setLoadingGraph(true);
      setGraphError(false);
      getKnowledgeGraph("", {
        textbookId: g.textbook_id || g.topic_key,
        level,
        subject,
        view: "full",
      })
        .then((r) => {
          if (r.status === "ok") {
            setNodes(r.nodes ?? []);
            setEdges(r.edges ?? []);
          } else {
            setNodes([]);
            setGraphError(true);
          }
        })
        .catch(() => setGraphError(true))
        .finally(() => setLoadingGraph(false));
    },
    [groups, level, subject],
  );

  // 章节树：part_of 边把概念/节收编进章节（节再归章，取传递闭包）
  const { chapters } = useMemo(() => {
    const memberOf = new Map<string, string>();
    for (const e of edges) {
      if (e.type === "part_of") memberOf.set(e.from, e.to);
    }
    const chapterNodes = nodes.filter((n) => n.kind === "chapter");
    const chapterIds = new Set(chapterNodes.map((c) => c.id));
    const resolveChapter = (id: string): string | null => {
      const seen = new Set<string>();
      let cur = memberOf.get(id);
      while (cur && !seen.has(cur)) {
        if (chapterIds.has(cur)) return cur;
        seen.add(cur);
        cur = memberOf.get(cur);
      }
      return null;
    };
    const map = new Map<string, { chapter: KnowledgeNode; concepts: KnowledgeNode[] }>();
    const c2ch = new Map<string, string>();
    for (const n of nodes) {
      if (n.kind !== "concept") continue;
      const ch = resolveChapter(n.id);
      c2ch.set(n.id, ch ?? "");
      if (!ch) continue;
      if (!map.has(ch)) {
        const cnode = chapterNodes.find((c) => c.id === ch);
        if (!cnode) continue;
        map.set(ch, { chapter: cnode, concepts: [] });
      }
      map.get(ch)!.concepts.push(n);
    }
    const list = [...map.values()].sort(
      (a, b) =>
        Number(a.chapter.metadata?.chapter_order ?? 0) -
        Number(b.chapter.metadata?.chapter_order ?? 0),
    );
    // 没挂到任何章的概念放一个"其他"组，避免遗漏
    const loose = nodes.filter(
      (n) => n.kind === "concept" && !c2ch.get(n.id),
    );
    if (loose.length) {
      list.push({ chapter: { id: "__loose", name: "—" } as KnowledgeNode, concepts: loose });
    }
    return { chapters: list, conceptToChapter: c2ch };
  }, [nodes, edges]);

  const selectedIds = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);

  const toggleConcept = (n: KnowledgeNode) => {
    if (selectedIds.has(n.id)) {
      onChange(selected.filter((s) => s.id !== n.id));
    } else {
      onChange([...selected, { id: n.id, name: n.name }]);
    }
  };

  const toggleChapter = (group: { chapter: KnowledgeNode; concepts: KnowledgeNode[] }) => {
    const allSelected =
      group.concepts.length > 0 && group.concepts.every((c) => selectedIds.has(c.id));
    if (allSelected) {
      const ids = new Set(group.concepts.map((c) => c.id));
      onChange(selected.filter((s) => !ids.has(s.id)));
    } else {
      const merged = [...selected];
      for (const c of group.concepts) {
        if (selectedIds.has(c.id)) continue;
        merged.push({ id: c.id, name: c.name });
      }
      onChange(merged);
    }
  };

  const searchHits = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return [];
    return nodes
      .filter((n) => n.kind === "concept" && n.name.toLowerCase().includes(q))
      .slice(0, 12);
  }, [nodes, search]);

  return (
    <div className="rounded-[10px] border border-border-light bg-surface px-3 py-2.5">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[0.72rem] font-medium text-fg-secondary">{t("title")}</span>
        {selected.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="shrink-0 cursor-pointer text-[0.68rem] text-muted transition-colors hover:text-danger"
          >
            {t("clear")}
          </button>
        )}
      </div>

      {selected.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {selected.slice(0, 12).map((s) => (
            <span
              key={s.id}
              className="inline-flex max-w-full items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-[0.68rem] text-accent-strong"
            >
              <span className="max-w-40 truncate">{s.name}</span>
              <button
                type="button"
                aria-label={`remove ${s.name}`}
                onClick={() => onChange(selected.filter((x) => x.id !== s.id))}
                className="cursor-pointer opacity-70 hover:opacity-100"
              >
                <X size={10} />
              </button>
            </span>
          ))}
          {selected.length > 12 && (
            <span className="self-center text-[0.68rem] text-muted">+{selected.length - 12}</span>
          )}
          <span className="self-center text-[0.68rem] text-muted">
            · {t("selected", selected.length)}
            {selected.length > 40 ? ` · ${t("overLimit")}` : ""}
          </span>
        </div>
      )}

      <div className="mb-2 flex flex-wrap gap-1.5">
        <select
          className={SELECT}
          value={level}
          onChange={(e) => {
            setLevel(e.target.value);
            setSubject("");
            setGroupId("");
            setNodes([]);
            setEdges([]);
          }}
        >
          <option value="">{t("level")}</option>
          {levels.map((l) => (
            <option key={l.name} value={l.name}>{l.name}</option>
          ))}
        </select>
        <select
          className={SELECT}
          value={subject}
          disabled={!level}
          onChange={(e) => {
            setSubject(e.target.value);
            setGroupId("");
            setNodes([]);
            setEdges([]);
          }}
        >
          <option value="">{t("subject")}</option>
          {subjectNames.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          className={SELECT}
          value={groupId}
          disabled={!subject}
          onChange={(e) => {
            setGroupId(e.target.value);
            if (e.target.value) loadGraph(e.target.value);
          }}
        >
          <option value="">{t("group")}</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}（{g.node_count} {t("concepts")}）
            </option>
          ))}
        </select>
      </div>

      {!groupId ? (
        <p className="py-1 text-[0.7rem] text-muted">{t("groupNone")}</p>
      ) : loadingGraph ? (
        <p className="py-1 text-[0.7rem] text-muted">{t("loading")}</p>
      ) : graphError ? (
        <p className="py-1 text-[0.7rem] text-warning">{t("loadFail")}</p>
      ) : (
        <>
          <div className="relative mb-2" ref={searchWrapRef}>
            <Search size={12} className="absolute top-2 left-2 text-muted" />
            <input
              value={search}
              onChange={(e) => { setSearch(e.target.value); setHitsDismissedFor(""); }}
              placeholder={t("searchPh")}
              className="h-7 w-full rounded-[8px] border border-border bg-surface pr-2 pl-6 text-xs text-fg outline-none placeholder:text-muted focus:border-accent"
            />
            {searchHits.length > 0 && hitsDismissedFor !== search && (
              <AnchoredPopover
                anchorRef={searchWrapRef}
                open
                onClose={() => setHitsDismissedFor(search)}
                placement="bottom-start"
                matchAnchorWidth
                className="z-10 max-h-44 overflow-y-auto rounded-[8px] border border-border bg-surface px-1 py-1 shadow-md"
              >
                {searchHits.map((n) => (
                  <button
                    key={n.id}
                    type="button"
                    onClick={() => toggleConcept(n)}
                    className={cn(
                      "block w-full cursor-pointer truncate rounded-[6px] px-2 py-1 text-left text-xs hover:bg-surface-hover",
                      selectedIds.has(n.id) && "text-accent-strong",
                    )}
                  >
                    {selectedIds.has(n.id) ? "✓ " : ""}
                    {n.name}
                  </button>
                ))}
              </AnchoredPopover>
            )}
          </div>
          {chapters.length === 0 ? (
            <p className="py-1 text-[0.7rem] text-muted">{t("empty")}</p>
          ) : (
            <div className="max-h-44 overflow-y-auto">
              {chapters.map((group) => {
                const allSelected =
                  group.concepts.length > 0 &&
                  group.concepts.every((c) => selectedIds.has(c.id));
                const someSelected =
                  group.concepts.some((c) => selectedIds.has(c.id));
                const open = expanded === group.chapter.id;
                return (
                  <div key={group.chapter.id} className="border-b border-border-light last:border-0">
                    <div className="flex items-center gap-2 py-1">
                      <button
                        type="button"
                        aria-label={t("expand")}
                        onClick={() => setExpanded(open ? null : group.chapter.id)}
                        className="shrink-0 cursor-pointer text-muted"
                      >
                        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      </button>
                      <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                        <input
                          type="checkbox"
                          checked={allSelected}
                          ref={(el) => {
                            if (el) el.indeterminate = !allSelected && someSelected;
                          }}
                          onChange={() => toggleChapter(group)}
                          className="size-3.5 cursor-pointer accent-[rgb(var(--accent))]"
                        />
                        <span className="truncate text-xs font-medium text-fg">
                          {group.chapter.id === "__loose" ? "" : group.chapter.name}
                        </span>
                        <span className="tnum shrink-0 text-[0.66rem] text-muted">
                          {group.concepts.length} {t("concepts")}
                        </span>
                      </label>
                    </div>
                    {open && (
                      <div className="grid grid-cols-1 gap-0.5 pb-1.5 pl-7 sm:grid-cols-2">
                        {group.concepts.map((c) => (
                          <label key={c.id} className="flex cursor-pointer items-center gap-1.5">
                            <input
                              type="checkbox"
                              checked={selectedIds.has(c.id)}
                              onChange={() => toggleConcept(c)}
                              className="size-3 cursor-pointer accent-[rgb(var(--accent))]"
                            />
                            <span
                              className={cn(
                                "truncate text-[0.7rem]",
                                selectedIds.has(c.id) ? "text-accent-strong" : "text-fg-secondary",
                              )}
                            >
                              {c.name}
                            </span>
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
