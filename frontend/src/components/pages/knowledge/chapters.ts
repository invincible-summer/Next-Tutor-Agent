// 章节视图数据模型：把「概念 + part_of 边 + chapter/section 节点」聚合成
// 章节/节两级导航结构。纯函数、无 React 依赖，便于单测；page.tsx 消费其输
// 出驱动渲染。节（课/篇目/小节）是可选中间层：概念→节→章 与 概念→章 两
// 种形状并存，旧图谱（无节）行为完全不变。
import type { KnowledgeEdge, KnowledgeNode } from "@/lib/types-modules";

export interface ChapterModel {
  /** 当前筛选范围内是否满足章节模式（有 chapter 节点且有 part_of 边） */
  hasChapters: boolean;
  /** 原始 chapter 节点（id → node） */
  chapterById: Map<string, KnowledgeNode>;
  /** 概念 id → 所属章节 id（经节层折叠：概念→节→章；仅 part_of 收录的概念） */
  chapterOf: Map<string, string>;
  /** 章节 id → 子概念列表（含挂节概念，按 id 排序，稳定） */
  childrenOf: Map<string, KnowledgeNode[]>;
  /** 章节总览节点：章节伪节点（kind==="chapter"，掌握度为子概念聚合）+ 无章节孤儿概念 */
  overviewNodes: KnowledgeNode[];
  /** 章节总览边：跨组 prerequisite 边聚合去重（忽略同章内部边） */
  overviewEdges: KnowledgeEdge[];
  /** 各章节子概念数（id → count，含 0 子概念章节） */
  childCount: Map<string, number>;
  /** 节（section）节点（id → node） */
  sectionById: Map<string, KnowledgeNode>;
  /** 章节 id → 节列表（构建顺序稳定） */
  sectionsOfChapter: Map<string, KnowledgeNode[]>;
  /** 节 id → 概念列表 */
  conceptsOfSection: Map<string, KnowledgeNode[]>;
  /** 章节 id → 直挂章的概念列表（不经节层；「本单元概念」虚拟卡数据源） */
  directConceptsOfChapter: Map<string, KnowledgeNode[]>;
  /** 各章节节数（id → count） */
  sectionCount: Map<string, number>;
}

/** 子概念掌握度聚合 → 章节卡片色（复用 stateColor 的四态语义）。 */
export function aggregateChapterState(children: KnowledgeNode[]): string {
  if (children.length === 0) return "";
  const states = children.map((c) => c.mastery?.state ?? "");
  const learned = states.filter((s) => s !== "");
  if (learned.length === 0) return ""; // 全部未学 → 灰
  const mis = learned.filter((s) => s === "misconception").length;
  // 误解加权：≥1/3 已学概念存在误解（或 ≥2 个）→ 偏红
  if (mis >= 2 || mis * 3 >= learned.length) return "misconception";
  const und = learned.filter((s) => s === "understood").length;
  // 已学的全部掌握 → 偏绿（允许还有未学概念）
  if (und === learned.length) return "understood";
  return "partial";
}

/**
 * 构建章节模型。
 * - 无 chapter 节点或无 part_of 边 → hasChapters=false，调用方回退平铺视图。
 * - 无 part_of 归属的孤儿概念以普通概念节点身份并入总览，保证混合图（新旧包并存）不丢节点。
 * - 节层可选：有节时概念经「概念→节→章」折叠归属；childrenOf/childCount/
 *   掌握度聚合均含挂节概念，旧（概念→章）形状数值不变。
 */
export function buildChapterModel(nodes: KnowledgeNode[], edges: KnowledgeEdge[]): ChapterModel {
  const ids = new Set(nodes.map((n) => n.id));
  const chapterById = new Map<string, KnowledgeNode>();
  for (const n of nodes) if (n.kind === "chapter") chapterById.set(n.id, n);
  const sectionById = new Map<string, KnowledgeNode>();
  for (const n of nodes) if (n.kind === "section") sectionById.set(n.id, n);

  // 直接父级（part_of）：概念→章 | 概念→节 | 节→章 全部收录（首边优先）。
  // 只收目标为章的边会丢弃概念→节：挂节概念折叠不出章（成为总览孤儿、
  // 与单元卡片同层），conceptsOfSection 也永远为空（下钻课文空白）。
  const directParent = new Map<string, string>();
  for (const e of edges) {
    if (e.type !== "part_of" || !ids.has(e.from) || !ids.has(e.to)) continue;
    if (!directParent.has(e.from)) directParent.set(e.from, e.to);
  }

  // 章归属折叠：概念→节→章（至多两级，环保护按无归属处理）
  const chapterOf = new Map<string, string>();
  const resolveChapter = (id: string): string | null => {
    let cur: string | null = id;
    for (let i = 0; cur && i < 3; i++) {
      const parent = directParent.get(cur);
      if (!parent) return null;
      if (chapterById.has(parent)) return parent;
      cur = parent;
    }
    return null;
  };
  for (const n of nodes) {
    if (n.kind === "chapter") continue;
    const ch = resolveChapter(n.id);
    if (ch) chapterOf.set(n.id, ch);
  }

  const hasChapters = chapterById.size > 0 && chapterOf.size > 0;
  const empty: ChapterModel = {
    hasChapters,
    chapterById,
    chapterOf,
    childrenOf: new Map(),
    overviewNodes: [],
    overviewEdges: [],
    childCount: new Map(),
    sectionById,
    sectionsOfChapter: new Map(),
    conceptsOfSection: new Map(),
    directConceptsOfChapter: new Map(),
    sectionCount: new Map(),
  };
  if (!hasChapters) return empty;

  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  // 节归属：章节 → 节列表（稳定序）；节 → 概念列表
  const sectionsOfChapter = new Map<string, KnowledgeNode[]>();
  for (const chId of chapterById.keys()) sectionsOfChapter.set(chId, []);
  const conceptsOfSection = new Map<string, KnowledgeNode[]>();
  const sectionOrder = new Map(nodes.map((n, i) => [n.id, i]));
  for (const [secId, sec] of sectionById) {
    const chId = chapterOf.get(secId);
    if (!chId) continue;
    sectionsOfChapter.get(chId)?.push(sec);
    conceptsOfSection.set(secId, []);
  }
  for (const arr of sectionsOfChapter.values()) {
    arr.sort((a, b) => (sectionOrder.get(a.id) ?? 0) - (sectionOrder.get(b.id) ?? 0));
  }

  // 子概念：概念直接挂章或经节挂章都计入章；概念挂节时同步登记到节
  const childrenOf = new Map<string, KnowledgeNode[]>();
  for (const chId of chapterById.keys()) childrenOf.set(chId, []);
  for (const [cid, chId] of chapterOf) {
    const n = nodeById.get(cid);
    if (!n) continue;
    if (n.kind === "section") continue;
    childrenOf.get(chId)?.push(n);
    const parent = directParent.get(cid);
    if (parent && conceptsOfSection.has(parent)) conceptsOfSection.get(parent)?.push(n);
  }
  for (const arr of childrenOf.values()) arr.sort((a, b) => a.id.localeCompare(b.id));

  // 直挂章的概念（不经节层）：有节章的下钻路径里它们没有卡片，由调用方
  // 以「本单元概念」虚拟卡承载，保证概念层完整可达
  const directConceptsOfChapter = new Map<string, KnowledgeNode[]>();
  for (const chId of chapterById.keys()) directConceptsOfChapter.set(chId, []);
  for (const [cid, chId] of chapterOf) {
    const n = nodeById.get(cid);
    if (!n || n.kind === "section") continue;
    const parent = directParent.get(cid);
    if (!parent || !conceptsOfSection.has(parent)) {
      directConceptsOfChapter.get(chId)?.push(n);
    }
  }
  for (const arr of directConceptsOfChapter.values()) arr.sort((a, b) => a.id.localeCompare(b.id));

  // 章节伪节点：掌握度 = 子概念聚合，难度 = 子概念均值（fallback 章节自身）
  const overviewNodes: KnowledgeNode[] = [];
  const childCount = new Map<string, number>();
  const sectionCount = new Map<string, number>();
  for (const [chId, ch] of chapterById) {
    const children = childrenOf.get(chId) ?? [];
    childCount.set(chId, children.length);
    sectionCount.set(chId, (sectionsOfChapter.get(chId) ?? []).length);
    const state = aggregateChapterState(children);
    const known = children.map((c) => c.mastery?.p_known).filter((p): p is number => p != null);
    const pKnown = known.length > 0 ? known.reduce((s, p) => s + p, 0) / known.length : 0;
    const diff =
      children.length > 0 ? children.reduce((s, c) => s + (c.difficulty || 1), 0) / children.length : ch.difficulty;
    overviewNodes.push({
      ...ch,
      difficulty: Math.round(diff * 10) / 10,
      mastery: state ? { p_known: Math.round(pKnown * 100) / 100, state } : null,
    });
  }
  // 孤儿概念/节（未被任何章节收编）原样并入总览
  for (const n of nodes) {
    if (n.kind === "chapter") continue;
    if (!chapterOf.has(n.id)) overviewNodes.push(n);
  }

  // 跨组 prerequisite 聚合去重；组 = 章节、节或孤儿概念自身
  const groupOf = (id: string): string | null =>
    chapterOf.get(id) ?? (ids.has(id) && !chapterById.has(id) ? id : null);
  const seen = new Set<string>();
  const overviewEdges: KnowledgeEdge[] = [];
  for (const e of edges) {
    if (e.type !== "prerequisite") continue;
    const a = groupOf(e.from);
    const b = groupOf(e.to);
    if (!a || !b || a === b) continue;
    const key = `${a}->${b}`;
    if (seen.has(key)) continue;
    seen.add(key);
    overviewEdges.push({ from: a, to: b, type: "prerequisite" });
  }

  return {
    hasChapters, chapterById, chapterOf, childrenOf, overviewNodes, overviewEdges,
    childCount, sectionById, sectionsOfChapter, conceptsOfSection,
    directConceptsOfChapter, sectionCount,
  };
}
