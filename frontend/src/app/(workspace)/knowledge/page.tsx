"use client";
// /knowledge 知识图谱页：M5 图谱画布 + M2 掌握度叠加 + M3 学习路径条 +
// M3 教学计划区（原 /plan 页迁入，C13：/plan 保留 redirect 深链）。
// 单学段单学科浏览（学段 segmented 单选默认账户学段，「自动」回落本科；学科必选，与教材组/卷
// 一样为客户端过滤、切换即时不重拉）；搜索按学段全量穿透，命中后自动把
// 学科/教材组范围切过去，结果面板 + ‹ i/N › 循环定位（匹配逻辑统一在
// components/.../search.ts）。
import { useCallback, useEffect, useMemo, useRef, useState, Suspense } from "react";
import { ChevronRight, Network, Sparkles } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useUIStore } from "@/lib/store";
import { useAuthStore } from "@/lib/auth-store";
import { makePageT } from "@/lib/i18n-page";
import {
  deleteCustomGraph,
  getCustomGraphs,
  getKnowledgeGraph,
  getKnowledgeTaxonomy,
  getLearningPath,
  getOrchPlan,
} from "@/lib/api-modules";
import type {
  CustomGraphMeta,
  KnowledgeGraphResp,
  KnowledgeNode,
  KnowledgeTaxonomyResp,
  LearningPathResp,
  OrchPlanSummary,
} from "@/lib/types-modules";
import { Skeleton, ErrorNote, EmptyState } from "@/components/ui/EmptyState";
import { Button } from "@/components/ui/Button";
import Link from "next/link";
import { ConfirmModal } from "@/components/ui/Modal";
import { KnowledgeGraphView } from "@/components/pages/knowledge/KnowledgeGraphView";
import { FilterBar, type FilterLevelInfo } from "@/components/pages/knowledge/FilterBar";
import { SearchBox } from "@/components/pages/knowledge/SearchBox";
import { buildChapterModel } from "@/components/pages/knowledge/chapters";
import { ConceptDrawer } from "@/components/pages/knowledge/ConceptDrawer";
import { LearningPathBar } from "@/components/pages/knowledge/LearningPathBar";
import { PlanSection } from "@/components/pages/knowledge/PlanSection";
import { CustomGraphList } from "@/components/pages/knowledge/CustomGraphList";
import { groupOfNode, searchConcepts } from "@/components/pages/knowledge/search";
import { STRINGS } from "./strings";

const CHAPTER_NODE_H = 40;
/** 学段默认回落值：账户学段缺失/为「自动」、或目录里没有该学段时取本科，
 *  与后端推荐学习路径（`_personalized_next` 自动→本科）口径一致。 */
const FALLBACK_LEVEL = "本科";

function KnowledgePageInner() {
  const lang = useUIStore((s) => s.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  // 账户学段驱动默认值：空串=「自动」（回落本科）。auth 水合晚于首帧时
  // profileGrade 变化会再次触发 fetchTaxonomy，校正默认学段/学科。
  const profileGrade = (useAuthStore((s) => s.user?.profile.grade) ?? "").trim();
  // 用户是否已手选学段/学科：置位后 taxonomy（重）到达不再改写默认值
  const stageTouched = useRef(false);
  // URL 深链（?concept=<图谱节点id>）：编排任务/差距行/测评跳转定位用。
  // useSearchParams 要求静态预渲染页包 Suspense（见文件底部导出）。
  const searchParams = useSearchParams();
  const deepLinkParam = searchParams.get("concept");
  const deepLinkDone = useRef(false);

  const [graph, setGraph] = useState<KnowledgeGraphResp | null>(null);
  const [taxonomy, setTaxonomy] = useState<KnowledgeTaxonomyResp | null>(null);
  const [gLoading, setGLoading] = useState(true);
  const [gErr, setGErr] = useState<string | null>(null);
  const [path, setPath] = useState<LearningPathResp | null>(null);
  const [pLoading, setPLoading] = useState(true);
  const [pErr, setPErr] = useState(false);

  /** 学段为必有状态：taxonomy 到达前为 null（不发图请求），默认本科；
   * 学科同样必选（单科显示）：初始与学段一起默认到该学段第一个学科 */
  const [level, setLevel] = useState<string | null>(null);
  const [subject, setSubject] = useState<string | null>(null);
  const [textbookId, setTextbookId] = useState<string | null>(null);
  const [fileId, setFileId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  /** 当前定位的搜索命中（结果面板选择 / ‹› 循环切换） */
  const [activeMatchId, setActiveMatchId] = useState<string | null>(null);
  const [prereqOnly, setPrereqOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drill, setDrill] = useState<string | null>(null);
  const [sectionDrill, setSectionDrill] = useState<string | null>(null);

  // --- M5.7 教材图谱列表（P6-A4：手动构建已移除，图谱只来自教材） ---
  const [customGraphs, setCustomGraphs] = useState<CustomGraphMeta[]>([]);
  const [customErr, setCustomErr] = useState<string | null>(null);
  /** 进行中的管理操作（`delete:topic_key`） */
  const [busyKey, setBusyKey] = useState<string | null>(null);
  /** 待二次确认的危险操作 */
  const [confirm, setConfirm] = useState<{ kind: "delete"; g: CustomGraphMeta } | null>(null);

  // M9 编排目标（L1 目标链）：概念抽屉反向展示"属于目标《X》·距目标还差 N 概念"。
  // 只读、失败静默（未设目标时抽屉不显示归属行）。
  const [orchPlan, setOrchPlan] = useState<OrchPlanSummary | null>(null);

  // effect 体内不做同步 setState（react-hooks/set-state-in-effect）：
  // 初始 loading 由 useState 初值覆盖，学段默认值在 taxonomy 回调里给定，
  // 学段切换的 loading 由 resetStageView（事件处理器）先行置位。
  const requestSeq = useRef(0);
  // 服务端只按学段取数（单学段浏览）：学科/教材组/卷在客户端过滤
  const fetchGraph = useCallback(() => {
    if (!level) return;
    const seq = ++requestSeq.current;
    getKnowledgeGraph("student_default", { level, view: "full" })
      .then((r) => {
        if (seq !== requestSeq.current) return;
        setGraph(r);
        setGErr(null);
      })
      .catch((e: unknown) => {
        if (seq === requestSeq.current) setGErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => { if (seq === requestSeq.current) setGLoading(false); });
  }, [level]);

  const fetchTaxonomy = useCallback(() => {
    getKnowledgeTaxonomy()
      .then((r) => {
        if (r.status !== "ok") return;
        setTaxonomy(r);
        // 学段默认值：账户学段优先；自动/缺失/目录没有时回落本科，再没有
        // 取目录第一个学段。仅未手选时写入（含 auth 晚到后的默认值校正）。
        const names = (r.levels ?? []).map((x) => x.name);
        const target = names.includes(profileGrade)
          ? profileGrade
          : names.includes(FALLBACK_LEVEL)
            ? FALLBACK_LEVEL
            : (names[0] ?? FALLBACK_LEVEL);
        if (!stageTouched.current) {
          setLevel(target);
          // 学科必选：默认与学段一起落到该学段第一个学科
          setSubject(
            (r.levels ?? []).find((x) => x.name === target)?.subjects[0]?.name ?? null,
          );
        }
      })
      .catch(() => {
        // 目录拉取失败也要解锁页面：回落默认学段（空图走 EmptyState）
        setLevel((cur) => cur ?? FALLBACK_LEVEL);
      });
  }, [profileGrade]);

  const fetchPath = useCallback(() => {
    getLearningPath()
      .then(setPath)
      .catch(() => setPErr(true))
      .finally(() => setPLoading(false));
  }, []);

  // 自定义图谱列表：失败静默（页面主数据是 graph，列表属增强信息）
  const fetchCustom = useCallback(() => {
    getCustomGraphs()
      .then((r) => {
        if (r.status === "ok") setCustomGraphs(r.graphs ?? []);
      })
      .catch(() => {});
  }, []);

  /** 构建 / 管理操作成功后统一回源 */
  const refreshAll = useCallback(() => {
    fetchGraph();
    fetchCustom();
  }, [fetchGraph, fetchCustom]);

  useEffect(() => {
    fetchGraph();
    fetchTaxonomy();
    fetchPath();
    fetchCustom();
    getOrchPlan().then(setOrchPlan).catch(() => {});
  }, [fetchGraph, fetchTaxonomy, fetchPath, fetchCustom]);

  const retryGraph = useCallback(() => {
    setGLoading(true);
    setGErr(null);
    fetchGraph();
  }, [fetchGraph]);

  const retryPath = useCallback(() => {
    setPLoading(true);
    setPErr(false);
    fetchPath();
  }, [fetchPath]);

  // --- 范围数据：学段全量（服务端过滤）→ 组/学科/卷（客户端过滤） ---

  const allNodes = useMemo(() => graph?.nodes ?? [], [graph]);
  const allEdges = useMemo(() => graph?.edges ?? [], [graph]);

  // M5 taxonomy is the authoritative navigation projection. Selecting a group
  // narrows to its stable topic_key prefix; no graph rebuild is involved.
  const taxonomyGroups = useMemo(
    () => (taxonomy?.levels ?? []).flatMap((lv) => lv.subjects.flatMap((sub) => sub.groups)),
    [taxonomy],
  );
  const selectedGroup = useMemo(
    () => taxonomyGroups.find((g) => g.id === textbookId) ?? null,
    [taxonomyGroups, textbookId],
  );
  const groupsByPrefix = useMemo(
    () => [...taxonomyGroups].sort((a, b) => b.node_prefix.length - a.node_prefix.length),
    [taxonomyGroups],
  );

  const groupNodes = useMemo(
    () => (selectedGroup ? allNodes.filter((n) => n.id.startsWith(selectedGroup.node_prefix)) : allNodes),
    [allNodes, selectedGroup],
  );
  const subjectNodes = useMemo(
    () => (subject ? groupNodes.filter((n) => n.subject === subject) : groupNodes),
    [groupNodes, subject],
  );
  // 卷过滤镜像后端口径：章节按 metadata.file_id/volume_id 归卷，概念/节经
  // part_of 传递收编（概念→节→章 与 概念→章 两种形状）
  const scopeNodes = useMemo(() => {
    if (!fileId) return subjectNodes;
    const chapterIds = new Set(
      allNodes
        .filter((n) => n.kind === "chapter"
          && String(n.metadata?.file_id ?? n.metadata?.volume_id ?? "") === fileId)
        .map((n) => n.id),
    );
    const memberIds = new Set<string>(chapterIds);
    let grew = true;
    while (grew) {
      grew = false;
      for (const e of allEdges) {
        if (e.type === "part_of" && memberIds.has(e.to) && !memberIds.has(e.from)) {
          memberIds.add(e.from);
          grew = true;
        }
      }
    }
    return subjectNodes.filter((n) => memberIds.has(n.id));
  }, [subjectNodes, fileId, allNodes, allEdges]);

  const scopeEdges = useMemo(() => {
    const ids = new Set(scopeNodes.map((n) => n.id));
    return allEdges.filter((e) => ids.has(e.from) && ids.has(e.to));
  }, [allEdges, scopeNodes]);

  const model = useMemo(() => buildChapterModel(scopeNodes, scopeEdges), [scopeNodes, scopeEdges]);

  // --- 分层筛选数据 ---

  const levelInfos: FilterLevelInfo[] = useMemo(
    () => (taxonomy?.levels ?? []).map((lv) => ({
      name: lv.name,
      groupCount: lv.subjects.reduce((acc, s) => acc + s.groups.length, 0),
    })),
    [taxonomy],
  );
  const subjects = useMemo(() => {
    const fromTaxonomy = (taxonomy?.levels ?? [])
      .filter((x) => !level || x.name === level)
      .flatMap((x) => x.subjects.map((sub) => sub.name));
    const present = new Set([...fromTaxonomy, ...allNodes.map((n) => n.subject).filter(Boolean)]);
    return [...present].sort();
  }, [taxonomy, level, allNodes]);
  const groups = useMemo(() => (taxonomy?.levels ?? [])
    .filter((x) => !level || x.name === level)
    .flatMap((x) => x.subjects
      .filter((sub) => !subject || sub.name === subject)
      .flatMap((sub) => sub.groups)), [taxonomy, level, subject]);

  // --- 视图状态重置 ---

  /** 清搜索态（输入清空 / 面板关闭），保留手动下钻 */
  const clearSearch = useCallback(() => {
    setSearch("");
    setActiveMatchId(null);
  }, []);

  /** 组内筛选变化的轻量重置：客户端过滤即时生效，不触碰加载态 */
  const resetView = useCallback(() => {
    clearSearch();
    setDrill(null);
    setSectionDrill(null);
    setSelectedId(null);
  }, [clearSearch]);

  /** 学段切换：作废在途请求并整段重拉 */
  const resetStageView = useCallback(() => {
    requestSeq.current += 1;
    setGLoading(true);
    setGErr(null);
    resetView();
  }, [resetView]);

  /** 清除教材组/卷两层筛选（清除按钮 / 面包屑首级共用）；学科必选，不在清除之列 */
  const clearScopeFilters = useCallback(() => {
    resetView();
    setTextbookId(null);
    setFileId(null);
  }, [resetView]);

  const pickLevel = useCallback((lv: string) => {
    if (lv === level) return;
    stageTouched.current = true;
    resetStageView();
    setLevel(lv);
    setTextbookId(null);
    setFileId(null);
    // 联动：学科必选——切学段后默认落到该学段第一个学科
    const avail = (taxonomy?.levels ?? []).find((x) => x.name === lv)?.subjects.map((s) => s.name) ?? [];
    setSubject(avail[0] ?? null);
  }, [level, resetStageView, taxonomy]);

  const pickSubject = useCallback((s: string | null) => {
    stageTouched.current = true;
    resetView();
    setSubject(s);
    setTextbookId(null);
    setFileId(null);
  }, [resetView]);

  const pickTextbook = useCallback((id: string | null) => {
    resetView();
    setTextbookId(id);
    setFileId(null);
  }, [resetView]);

  const pickFile = useCallback((id: string | null) => {
    resetView();
    setFileId(id);
  }, [resetView]);

  // --- 搜索：穿透筛选定位概念（学段全量上匹配，命中后自动把范围切过去） ---

  const matches = useMemo(() => searchConcepts(allNodes, search), [allNodes, search]);
  const matchedSet = useMemo(() => new Set(matches.map((n) => n.id)), [matches]);
  const q = search.trim().toLowerCase();

  // 学段全量的 概念/节 → 直接父级（part_of）映射：概念→节→章 折叠用于搜索命中下钻
  const chapterOf = useMemo(() => {
    const direct = new Map<string, string>();
    for (const e of allEdges) {
      if (e.type === "part_of") direct.set(e.from, e.to);
    }
    const byId = new Map(allNodes.map((n) => [n.id, n]));
    const folded = new Map<string, string>();
    for (const n of allNodes) {
      if (n.kind === "chapter") continue;
      let cur: string | undefined = direct.get(n.id);
      for (let i = 0; cur && i < 3; i++) {
        const parent = byId.get(cur);
        if (!parent) break;
        if (parent.kind === "chapter") {
          folded.set(n.id, cur);
          break;
        }
        cur = direct.get(cur);
      }
    }
    return folded;
  }, [allNodes, allEdges]);

  const pickMatch = useCallback((n: KnowledgeNode) => {
    setActiveMatchId(n.id);
    // 命中可能位于当前筛选范围之外：把范围切到该概念所属教材组（无组则清空组筛选）
    const group = groupOfNode(n.id, groupsByPrefix);
    let parentSubject: string | null = null;
    if (group) {
      for (const lv of taxonomy?.levels ?? []) {
        const sub = lv.subjects.find((s) => s.groups.some((g) => g.id === group.id));
        if (sub) {
          parentSubject = sub.name;
          break;
        }
      }
    }
    setTextbookId(group?.id ?? null);
    // 学科必选：命中无学科信息时保留当前学科（不回落「全部」）
    const nextSubject = group ? (parentSubject ?? n.subject) : n.subject;
    setSubject((cur) => nextSubject ?? cur);
    setFileId(null);
    // 章节视图下钻：节命中 → 下钻到节层；概念命中 → 折叠到所属章（进节层再定位）
    setDrill(n.kind === "chapter" ? n.id : (chapterOf.get(n.id) ?? null));
    setSectionDrill(n.kind === "section" ? n.id : null);
  }, [groupsByPrefix, taxonomy, chapterOf]);

  const stepMatch = useCallback((dir: 1 | -1) => {
    if (matches.length === 0) return;
    const idx = activeMatchId ? matches.findIndex((n) => n.id === activeMatchId) : -1;
    const next = idx === -1
      ? (dir === 1 ? matches[0] : matches[matches.length - 1])
      : matches[(idx + dir + matches.length) % matches.length];
    if (next) pickMatch(next);
  }, [matches, activeMatchId, pickMatch]);

  // --- URL 深链定位：?concept=<图谱节点 id>（编排差距行/周概念/任务跳转入口）---
  // 等图谱与 taxonomy 就绪后一次性定位：切范围 + 下钻 + 高亮 + 打开抽屉。
  // setState 走微任务回调（effect 体内不做同步 setState 的仓库纪律）。
  useEffect(() => {
    if (deepLinkDone.current || !deepLinkParam) return;
    if (allNodes.length === 0 || !taxonomy) return;
    deepLinkDone.current = true;
    const node = allNodes.find((n) => n.id === deepLinkParam);
    if (!node) return;
    void Promise.resolve().then(() => {
      pickMatch(node);
      setSelectedId(node.id);
    });
  }, [deepLinkParam, allNodes, taxonomy, pickMatch]);

  // 概念抽屉的目标归属（L1 目标链反查）：属于目标链/目标概念时显示
  // "属于目标《X》· 距目标还差 N 个概念 · 当前第 L 层"。多目标下逐个
  // 目标反查，命中第一个包含该概念的目标。
  const drawerGoal = useMemo(() => {
    const goals = (orchPlan?.goals ?? []).filter((g) => !!g.title);
    const states = orchPlan?.goal_states ?? [];
    if (!selectedId || goals.length === 0) return null;
    // 命中第一个包含该概念的目标（flatMap 过滤未命中的目标）
    const hit = goals.flatMap((goal, i) => {
      // 优先 goal_id 精确配对；无 id 的旧数据按下标对齐
      const gs = (goal.id
        ? states.find((s) => s.goal_id === goal.id)
        : undefined) ?? states[i];
      const req = gs?.required_skills ?? [];
      const idx = req.indexOf(selectedId);
      const gap = (gs?.gaps ?? []).find((g) => g.skill_id === selectedId);
      const isTarget = (goal.target_concept_ids ?? []).includes(selectedId);
      if (idx === -1 && !gap && !isTarget) return [];
      return [{
        title: goal.title ?? "",
        layer: gap?.layer ?? 0,
        remaining: idx >= 0 ? req.length - idx : null,
        isTarget,
      }];
    });
    return hit[0] ?? null;
  }, [selectedId, orchPlan]);

  // --- 自定义图谱管理操作 ---

  /** 查看：切到该图谱的学段并选中其教材组（学科必选：无学科信息时回落该学段第一个学科） */
  const viewCustom = useCallback((g: CustomGraphMeta) => {
    stageTouched.current = true;
    resetStageView();
    const lv = g.level || "其他";
    setLevel(lv);
    setSubject(g.subject
      || ((taxonomy?.levels ?? []).find((x) => x.name === lv)?.subjects[0]?.name ?? null));
    setTextbookId(taxonomyGroups.find((x) => x.topic_key === g.topic_key)?.id ?? null);
    setFileId(null);
  }, [resetStageView, taxonomy, taxonomyGroups]);

  const runAction = useCallback(
    (key: string, fn: () => Promise<{ status: string; message?: string }>, okStatuses: string[]) => {
      setBusyKey(key);
      setCustomErr(null);
      fn()
        .then((r) => {
          if (okStatuses.includes(r.status)) refreshAll();
          else setCustomErr(r.message || tr("custom.actionFail"));
        })
        .catch(() => setCustomErr(tr("custom.actionFail")))
        .finally(() => {
          setBusyKey(null);
          setConfirm(null);
        });
    },
    [refreshAll, tr],
  );

  const doDelete = useCallback(
    (g: CustomGraphMeta) => {
      runAction(`delete:${g.topic_key}`, () => deleteCustomGraph(g.topic_key), ["ok"]);
    },
    [runAction],
  );

  // --- 视图模式：平铺（无 chapter）/ 章节总览 / 章内概念 ---

  const drilled = drill && model.chapterById.has(drill) ? drill : null;
  const sections = useMemo(
    () => (drilled ? (model.sectionsOfChapter.get(drilled) ?? []) : []),
    [model, drilled],
  );
  // 有节的章：下钻先进「节卡片」层（课/篇目），再进节内概念；无节旧图谱直达概念
  const showSectionLayer = sections.length > 0;
  // 直挂章概念（不经课文层）在节层没有卡片：补一张「本单元概念」虚拟卡承载，
  // 保证概念层完整可达（虚拟 id = `${drilled}:direct`，kind 借 section 走下钻分支）
  const directConcepts = useMemo(
    () => (drilled ? (model.directConceptsOfChapter.get(drilled) ?? []) : []),
    [model, drilled],
  );
  const directCard = useMemo<KnowledgeNode | null>(() => {
    const ch = drilled ? model.chapterById.get(drilled) : null;
    if (!ch || !showSectionLayer || directConcepts.length === 0) return null;
    return { ...ch, id: `${drilled}:direct`, kind: "section", name: tr("unitDirectConcepts"), aliases: [], mastery: null };
  }, [drilled, model, showSectionLayer, directConcepts, tr]);
  const sectionCards = useMemo(
    () => (directCard ? [...sections, directCard] : sections),
    [sections, directCard],
  );
  const drilledSection = sectionDrill
    && (sectionDrill === directCard?.id || model.sectionById.has(sectionDrill))
    ? sectionDrill : null;

  // 章节总览的搜索匹配：章节名/ID 命中或任一子概念命中 → 章节卡片高亮
  const overviewMatched = useMemo(() => {
    const s = new Set<string>();
    if (!q) return s;
    for (const n of model.overviewNodes) {
      if (n.kind === "chapter") {
        const hay = `${n.name} ${n.id}`.toLowerCase();
        const childHit = (model.childrenOf.get(n.id) ?? []).some((c) => matchedSet.has(c.id))
          || (model.sectionsOfChapter.get(n.id) ?? []).some((c) => matchedSet.has(c.id));
        if (hay.includes(q) || childHit) s.add(n.id);
      } else if (matchedSet.has(n.id)) {
        s.add(n.id);
      }
    }
    return s;
  }, [q, model, matchedSet]);

  // 节卡片的搜索匹配：节名命中或节内概念命中（虚拟卡 = 直挂章概念命中）
  const sectionMatched = useMemo(() => {
    const s = new Set<string>();
    if (!q) return s;
    for (const sec of sectionCards) {
      const hay = `${sec.name} ${sec.id}`.toLowerCase();
      const childHit = sec.id === directCard?.id
        ? directConcepts.some((c) => matchedSet.has(c.id))
        : (model.conceptsOfSection.get(sec.id) ?? []).some((c) => matchedSet.has(c.id));
      if (hay.includes(q) || matchedSet.has(sec.id) || childHit) s.add(sec.id);
    }
    return s;
  }, [q, sectionCards, directCard, directConcepts, model, matchedSet]);

  const view = useMemo(() => {
    if (!model.hasChapters) {
      return { kind: "flat" as const, nodes: scopeNodes, edges: scopeEdges };
    }
    if (!drilled) {
      return { kind: "chapters" as const, nodes: model.overviewNodes, edges: model.overviewEdges };
    }
    // 有节的章：先展示节卡片（课/篇目 + 本单元概念虚拟卡），点击节再进其概念
    if (showSectionLayer && !drilledSection) {
      return { kind: "sections" as const, nodes: sectionCards, edges: [] };
    }
    // 节内概念 = 挂该节的概念 + 该章未挂节的直达概念（仅整章下钻时并入）
    const children = drilledSection
      ? (drilledSection === directCard?.id
        ? directConcepts
        : (model.conceptsOfSection.get(drilledSection) ?? []))
      : (model.childrenOf.get(drilled) ?? []);
    const ids = new Set(children.map((c) => c.id));
    return {
      kind: "concepts" as const,
      nodes: children,
      edges: scopeEdges.filter((e) => e.type !== "part_of" && ids.has(e.from) && ids.has(e.to)),
    };
  }, [model, drilled, drilledSection, showSectionLayer, sectionCards, directCard, directConcepts, scopeNodes, scopeEdges]);

  const chapterSubtitle = useCallback(
    (n: KnowledgeNode) => {
      if (n.kind !== "chapter") return undefined;
      const secs = model.sectionCount.get(n.id) ?? 0;
      const count = `${secs > 0 ? `${secs} ${tr("statSections")} · ` : ""}${model.childCount.get(n.id) ?? 0} ${tr("statConcepts")}`;
      // 跨教材范围只在展示层补来源，不污染持久化章节名/稳定 ID。
      if (!selectedGroup) {
        const source = groupsByPrefix.find((group) => n.id.startsWith(group.node_prefix));
        if (source) return `${source.name} · ${count}`;
      }
      return count;
    },
    [groupsByPrefix, model, selectedGroup, tr],
  );

  // 节卡片副标题：节内概念数（语文篇目下常挂若干知识点概念；虚拟卡计直挂概念数）
  const sectionSubtitle = useCallback(
    (n: KnowledgeNode) => n.id === directCard?.id
      ? `${directConcepts.length} ${tr("statConcepts")}`
      : `${model.conceptsOfSection.get(n.id)?.length ?? 0} ${tr("statConcepts")}`,
    [directCard, directConcepts, model, tr],
  );

  const onSelectNode = useCallback(
    (n: KnowledgeNode) => {
      if (view.kind === "chapters" && n.kind === "chapter") {
        setDrill(n.id);
        setSectionDrill(null);
      } else if (view.kind === "sections" && n.kind === "section") {
        // 零概念课文下钻只会得到空画布：改为打开详情抽屉（页码/所属/结构数据）
        const count = n.id === directCard?.id
          ? directConcepts.length
          : (model.conceptsOfSection.get(n.id)?.length ?? 0);
        if (count === 0) setSelectedId(n.id);
        else setSectionDrill(n.id);
      } else {
        setSelectedId(n.id);
      }
    },
    [view.kind, directCard, directConcepts, model],
  );

  const conceptCount = useMemo(
    () => scopeNodes.filter((n) => n.kind === "concept").length,
    [scopeNodes],
  );
  const sectionTotal = useMemo(
    () => scopeNodes.filter((n) => n.kind === "section").length,
    [scopeNodes],
  );

  const graphStatus = graph?.status;
  const graphMessage = (graph as (KnowledgeGraphResp & { message?: string }) | null)?.message;
  const isEmpty = graphStatus === "ok" && scopeNodes.length === 0;
  const isDisabled = graphStatus === "disabled";
  const isApiError = graphStatus != null && graphStatus !== "ok" && graphStatus !== "disabled";

  return (
    <div className="page-in h-full overflow-y-auto p-5">
      <div className="mx-auto flex w-full max-w-[1200px] flex-col gap-3">
        {/* 页头 */}
        <div className="flex shrink-0 items-end justify-between gap-3">
          <div>
            <h1 className="font-serif text-xl font-semibold text-fg">{tr("title")}</h1>
            <p className="mt-0.5 text-xs text-muted">{tr("desc")}</p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {graphStatus === "ok" && (
              <span className="tnum text-xs text-muted">
                {model.hasChapters && `${model.chapterById.size} ${tr("statChapters")} · `}
                {sectionTotal > 0 && `${sectionTotal} ${tr("statSections")} · `}
                {conceptCount} {tr("statConcepts")} · {scopeEdges.length} {tr("statEdges")}
                {(graph?.learned_edges ?? 0) > 0 && ` · ${graph?.learned_edges} ${tr("statLearned")}`}
              </span>
            )}
            <Link href="/resources/textbooks">
              <Button size="sm" icon={<Sparkles size={13} />}>
                {tr("build.open.textbook")}
              </Button>
            </Link>
          </div>
        </div>

        {/* 分层筛选：行 1 学段单选 tabs + 搜索；行 2 学科/教材组/卷下拉 */}
        {levelInfos.length > 0 && (
          <FilterBar
            levels={levelInfos}
            level={level}
            onPickLevel={pickLevel}
            subjects={subjects}
            subject={subject}
            onPickSubject={pickSubject}
            groups={groups}
            textbookId={textbookId}
            onPickTextbook={pickTextbook}
            volumes={selectedGroup?.volumes ?? []}
            fileId={fileId}
            onPickFile={pickFile}
            hasScopeFilters={Boolean(textbookId || fileId)}
            onClearFilters={clearScopeFilters}
            showPrereq={scopeNodes.length > 60}
            prereqOnly={prereqOnly}
            onTogglePrereq={() => setPrereqOnly((v) => !v)}
            right={
              <SearchBox
                value={search}
                onChange={setSearch}
                matches={matches}
                groupOf={(n) => groupOfNode(n.id, groupsByPrefix)}
                activeMatchId={activeMatchId}
                onPick={pickMatch}
                onStep={stepMatch}
                onClear={clearSearch}
                tr={tr}
              />
            }
            tr={tr}
          />
        )}

        {/* 教材图谱管理（列表为空时不渲染；P6-A4 起图谱只来自教材） */}
        <CustomGraphList
          graphs={customGraphs}
          tr={tr}
          busyKey={busyKey}
          onView={viewCustom}
          onDelete={(g) => setConfirm({ kind: "delete", g })}
        />
        {customErr && <p className="shrink-0 text-xs text-danger">{customErr}</p>}

        {/* 下钻面包屑：学段 / 学科 / 章节名 / 节名（逐级返回；筛选为「全部」时回显章节自身的学段/学科） */}
        {drilled && (
          <div data-testid="kg-breadcrumb" className="flex shrink-0 items-center gap-1 text-xs text-muted">
            <button
              onClick={clearScopeFilters}
              className="cursor-pointer rounded px-1 py-0.5 transition-colors hover:text-accent"
            >
              {(() => {
                const lv = level ?? model.chapterById.get(drilled)?.level;
                return lv ? tr(`level.${lv}`, lv) : tr("all");
              })()}
            </button>
            <ChevronRight size={12} />
            <button
              onClick={() => {
                setDrill(null);
                setSectionDrill(null);
                clearSearch();
              }}
              className="cursor-pointer rounded px-1 py-0.5 transition-colors hover:text-accent"
            >
              {subject ?? model.chapterById.get(drilled)?.subject ?? tr("all")}
            </button>
            <ChevronRight size={12} />
            {showSectionLayer ? (
              <button
                onClick={() => {
                  setSectionDrill(null);
                  clearSearch();
                }}
                className="cursor-pointer rounded px-1 py-0.5 transition-colors hover:text-accent"
              >
                {model.chapterById.get(drilled)?.name ?? drilled}
              </button>
            ) : (
              <span className="rounded px-1 py-0.5 font-medium text-fg">
                {model.chapterById.get(drilled)?.name ?? drilled}
              </span>
            )}
            {showSectionLayer && drilledSection && (
              <>
                <ChevronRight size={12} />
                <span className="rounded px-1 py-0.5 font-medium text-fg">
                  {model.sectionById.get(drilledSection)?.name
                    ?? (drilledSection === directCard?.id ? tr("unitDirectConcepts") : drilledSection)}
                </span>
              </>
            )}
          </div>
        )}

        {/* 画布区：定高（视口比例 + 最小高度），标签多时整页滚动而非压缩画布 */}
        <div className="h-[62vh] min-h-[420px] shrink-0">
          {gLoading && <Skeleton className="h-full w-full" />}
          {!gLoading && gErr && (
            <ErrorNote message={`${tr("loadFail")}: ${gErr}`} retry={retryGraph} />
          )}
          {!gLoading && !gErr && isApiError && (
            <ErrorNote message={`${tr("loadFail")}${graphMessage ? `: ${graphMessage}` : ""}`} retry={retryGraph} />
          )}
          {!gLoading && !gErr && isDisabled && (
            <EmptyState
              icon={<Network size={28} />}
              title={tr("disabledTitle")}
              desc={tr("disabledDesc")}
              className="h-full"
            />
          )}
          {!gLoading && !gErr && isEmpty && (
            <EmptyState
              icon={<Network size={28} />}
              title={tr("emptyTitle")}
              desc={tr("emptyDesc")}
              className="h-full"
            />
          )}
          {/* 零概念课文/单元：概念层 0 节点时给空态提示，不留空白画布 */}
          {!gLoading && !gErr && graphStatus === "ok" && scopeNodes.length > 0
            && view.kind === "concepts" && view.nodes.length === 0 && (
            <EmptyState
              icon={<Network size={28} />}
              title={tr("emptySectionConcepts")}
              className="h-full"
            />
          )}
          {!gLoading && !gErr && graphStatus === "ok" && scopeNodes.length > 0
            && !(view.kind === "concepts" && view.nodes.length === 0) && (
            <KnowledgeGraphView
              nodes={view.nodes}
              edges={view.edges}
              hasQuery={q.length > 0}
              focusId={activeMatchId}
              prereqOnly={prereqOnly}
              selectedId={selectedId}
              onSelect={onSelectNode}
              lang={lang}
              resetLabel={tr("resetView")}
              nodeH={view.kind === "chapters" || view.kind === "sections" ? CHAPTER_NODE_H : undefined}
              subtitle={view.kind === "chapters"
                ? chapterSubtitle
                : view.kind === "sections" ? sectionSubtitle : undefined}
              matchedIds={view.kind === "chapters"
                ? overviewMatched
                : view.kind === "sections" ? sectionMatched : matchedSet}
            />
          )}
        </div>

        {/* 底部学习路径条 */}
        <LearningPathBar tr={tr} path={path} loading={pLoading} error={pErr} onRetry={retryPath} />

        {/* 教学计划区（原 /plan 页迁入：模式状态机 + 难度表盘 + 完整路径两栏 + 教学日志） */}
        <div className="mt-2 shrink-0 border-t border-border-light pt-4">
          <PlanSection path={path} pathLoading={pLoading} pathError={pErr} lang={lang} />
        </div>
      </div>

      {/* 概念详情抽屉 */}
      <ConceptDrawer
        id={selectedId}
        lang={lang}
        tr={tr}
        goal={drawerGoal}
        onClose={() => setSelectedId(null)}
        onNavigate={(cid) => setSelectedId(cid)}
      />

      {/* 危险操作二次确认（删除图谱） */}
      <ConfirmModal
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        onConfirm={() => {
          if (confirm?.kind === "delete") doDelete(confirm.g);
        }}
        title={tr("custom.delete.confirm.title")}
        desc={
          confirm
            ? tr("custom.delete.confirm.desc").replace("%t", confirm.g.topic)
            : ""
        }
        confirmText={tr("common.confirm", "确认")}
        cancelText={tr("common.cancel", "取消")}
      />
    </div>
  );
}

/** useSearchParams 需要 Suspense 边界才能参与静态预渲染。 */
export default function KnowledgePage() {
  return (
    <Suspense>
      <KnowledgePageInner />
    </Suspense>
  );
}
