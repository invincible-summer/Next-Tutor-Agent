"use client";
// /memory 记忆中心（plan §14.1）：两个清晰区域——
// ①学习档案：按工作区展示统一学习评价（学科叙述/覆盖/近期变化/下一步；
//   Tabs：近期变化=证据时间线、对话记录=本区来源会话、教材概念=主张列表）。
//   它取代旧学习评价/六维/Bloom 弱项展示，不是在旧区域下再加一张新卡。
// ②AI 记忆与偏好：提示词记忆（跨对话画像）+ 工作区共同记忆 + 程序性记忆
//   + 历史审计（旧版情景/语义只读陈列，C4 合并）。
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Archive,
  ChevronDown,
  ChevronRight,
  Info,
  Layers,
  MessagesSquare,
  Users,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { Tabs } from "@/components/ui/Tabs";
import { EmptyState, ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { Pager } from "@/components/ui/Pager";
import { EpisodeTimeline } from "@/components/pages/memory/EpisodeTimeline";
import { SemanticFacts } from "@/components/pages/memory/SemanticFacts";
import { StrategyBars } from "@/components/pages/memory/StrategyBars";
import { EvidenceTimeline } from "@/components/learning-evaluation/EvidenceTimeline";
import { NextProbeAction } from "@/components/learning-evaluation/NextProbeAction";
import { SemanticEvaluationPanel } from "@/components/learning-evaluation/SemanticEvaluationPanel";
import { StatusNote } from "@/components/learning-evaluation/StatusNote";
import {
  getEpisodes,
  getEvalConcepts,
  getEvalEvidence,
  getEvalSessionEvidence,
  getEvalSessions,
  getEvalWorkspace,
  getEvalWorkspaces,
  getProceduralMemory,
  getSemanticMemory,
} from "@/lib/api-modules";
import {
  getPromptMemoryProfile,
  getWorkspace,
  listSessions,
  listWorkspaces,
  setPromptMemoryWindow,
} from "@/lib/api";
import { makePageT } from "@/lib/i18n-page";
import { useUIStore } from "@/lib/store";
import { et, evalStateTone, type Lang } from "@/lib/evaluation-labels";
import { fmtDate, relTime } from "@/lib/format";
import type {
  ConceptEvaluationView,
  Episode,
  EvalNextProbe,
  EvalSessionEvidenceItem,
  EvalSessionItem,
  EvalSourceTimelineItem,
  ProceduralStrategy,
  SemanticFact,
  WorkspaceEvaluationListItem,
  WorkspaceEvaluationSummary,
} from "@/lib/types-modules";
import type { PromptMemoryProfile, WorkspaceItem } from "@/lib/types";
import { STRINGS } from "./strings";

const PAGE_SIZE = 100;

interface EpisodesState {
  status: string;
  list: Episode[];
  hasMore: boolean;
}

function fmtIso(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** ②提示词记忆区：core_profile 三字段（current_level 已随旧水平推断删除）+ 注入字符。 */
function PromptMemorySection({
  profile,
  sessionTitles,
  windowValue,
  maxWindow,
  onWindowChange,
  onWindowBlur,
  tr,
  lang,
}: {
  profile: PromptMemoryProfile | null;
  sessionTitles: Map<string, string>;
  windowValue: number;
  maxWindow: number;
  onWindowChange: (n: number) => void;
  onWindowBlur: () => void;
  tr: (key: string, fallback?: string) => string;
  lang: string;
}) {
  if (!profile) return null;
  const fields: Array<[string, string]> = [
    ["learning_summary", tr("pm.field.learning_summary")],
    ["tone_preference", tr("pm.field.tone_preference")],
    ["explanation_preference", tr("pm.field.explanation_preference")],
  ];
  const gen = profile.compaction_generation ?? 0;
  const lastAt = profile.last_compacted_at ?? 0;
  return (
    <Card>
      <CardHeader
        icon={<Layers size={16} className="text-accent" />}
        title={tr("pm.title")}
        desc={tr("pm.desc")}
        right={
          <span className="tnum text-[11px] text-muted">
            {tr("pm.inject")} ≈ {profile.directive_chars} {tr("pm.chars")}
          </span>
        }
      />
      <p className="mb-3 rounded-[8px] bg-info/8 px-3 py-2 text-[0.72rem] leading-relaxed text-info">
        {tr("pm.note")}
      </p>
      <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {fields.map(([key, label]) => (
          <div key={key} className="rounded-[8px] border border-border-light bg-surface px-3 py-2">
            <div className="mb-0.5 text-[0.68rem] font-medium text-fg-secondary">{label}</div>
            <div className="text-xs leading-relaxed text-fg">
              {profile.core_profile?.[key]?.trim() || tr("pm.field.empty")}
            </div>
          </div>
        ))}
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.7rem] text-muted">
        <label className="flex items-center gap-2">
          {tr("pm.window")}
          <input type="number" min={5} max={maxWindow} value={windowValue}
            onChange={(e) => onWindowChange(Number(e.target.value))}
            onBlur={onWindowBlur}
            className="h-7.5 w-20 rounded-[7px] border border-border bg-surface px-2 text-xs text-fg" />
        </label>
        <span className="tnum">
          {tr("pm.compacted").replace("%n", String(profile.compacted_session_count ?? 0))}
        </span>
        {gen > 0 && (
          <span className="tnum">
            {tr("pm.generation").replace("%n", String(gen))}
            {lastAt > 0 ? ` · ${relTime(lastAt, lang === "en" ? "en" : "zh")}` : ""}
          </span>
        )}
      </div>
      <div>
        <p className="mb-1 text-[0.7rem] font-medium text-fg-secondary">{tr("pm.sessions")}</p>
        {profile.recent_sessions?.length ? (
          <ul className="divide-y divide-border-light">
            {profile.recent_sessions.map((s) => (
              <li key={s.session_id} className="flex items-center gap-2 py-1.5">
                <Badge tone={s.has_contribution ? "accent" : "outline"}>
                  {s.has_contribution ? tr("pm.contributed") : tr("pm.nocontrib")}
                </Badge>
                <span className="min-w-0 flex-1 truncate text-xs text-fg">
                  {sessionTitles.get(s.session_id) || tr("pm.untitled")}
                </span>
                {s.workspace_id && (
                  <Badge tone="info">{tr("pm.inWorkspace")}</Badge>
                )}
                <span className="tnum shrink-0 text-[0.66rem] text-muted">
                  {fmtDate(s.updated_at)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-1 text-[0.72rem] text-muted">{tr("pm.sessions.empty")}</p>
        )}
        {(profile.compacted_session_count ?? 0) > 0 && (
          <p className="mt-1 text-[0.66rem] text-muted">{tr("pm.compacted.note")}</p>
        )}
      </div>
    </Card>
  );
}

/** ②工作区共同记忆区：各工作区展开查看共享学习摘要（LLM 结构化文本）。 */
function WorkspaceMemorySection({
  tr,
  lang,
}: {
  tr: (key: string, fallback?: string) => string;
  lang: "zh" | "en";
}) {
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[] | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ id: string; text: string; at: number } | null>(null);

  useEffect(() => {
    let alive = true;
    listWorkspaces()
      .then((r) => alive && setWorkspaces(r.workspaces ?? []))
      .catch(() => alive && setWorkspaces([]));
    return () => {
      alive = false;
    };
  }, []);

  const toggle = (id: string) => {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    if (!detail || detail.id !== id) {
      getWorkspace(id)
        .then((d) => setDetail({ id, text: d.public_memory ?? "", at: d.public_memory_updated_at ?? 0 }))
        .catch(() => setDetail({ id, text: "", at: 0 }));
    }
  };

  return (
    <Card>
      <CardHeader
        icon={<Users size={16} className="text-accent" />}
        title={tr("ws.title")}
        desc={tr("ws.desc")}
      />
      {workspaces === null ? (
        <p className="py-2 text-xs text-muted">{tr("ws.loading")}</p>
      ) : workspaces.length === 0 ? (
        <EmptyState title={tr("ws.empty")} desc={tr("ws.empty.desc")} />
      ) : (
        <ul className="divide-y divide-border-light">
          {workspaces.map((w) => (
            <li key={w.workspace_id}>
              <button
                type="button"
                onClick={() => toggle(w.workspace_id)}
                className="flex w-full cursor-pointer items-center gap-2 py-2 text-left"
              >
                {expanded === w.workspace_id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">{w.name}</span>
                <Badge tone={w.has_memory ? "accent" : "outline"}>
                  {w.has_memory ? tr("ws.hasMemory") : tr("ws.noMemory")}
                </Badge>
                <span className="tnum text-[0.66rem] text-muted">{w.session_count}</span>
              </button>
              {expanded === w.workspace_id && (
                <div className="pb-2.5 pl-5">
                  {detail?.id === w.workspace_id && detail.text.trim() ? (
                    <>
                      <pre className="max-h-64 overflow-y-auto rounded-[8px] bg-surface-sunken p-3 text-[0.72rem] leading-relaxed whitespace-pre-wrap text-fg-secondary">
                        {detail.text}
                      </pre>
                      {detail.at > 0 && (
                        <p className="mt-1 text-[0.66rem] text-muted">
                          {tr("ws.updated")} {relTime(detail.at, lang)}
                        </p>
                      )}
                    </>
                  ) : detail?.id === w.workspace_id ? (
                    <p className="py-1 text-[0.72rem] text-muted">{tr("ws.noMemory.desc")}</p>
                  ) : (
                    <p className="py-1 text-[0.72rem] text-muted">{tr("ws.loading")}</p>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** ①学习档案区：工作区选择 + 叙述/覆盖/下一步 + 三个内容 Tabs。
 *  R09（update_plan §4）：四个区域（总览/时间线/会话/概念）独立加载与
 *  失败重试——一个子请求 422/失败不能把其他成功区域一起抹成空态；
 *  概念走服务端筛选 + 分页（统一 limit ≤100 上限），不拉全量。 */
function LearningArchiveRegion({ tr, lang }: { tr: (k: string, f?: string) => string; lang: Lang }) {
  const router = useRouter();
  const [wss, setWss] = useState<WorkspaceEvaluationListItem[] | null>(null);
  const [wsId, setWsId] = useState("");
  const [summary, setSummary] = useState<WorkspaceEvaluationSummary | null>(null);
  const [summaryErr, setSummaryErr] = useState(false);
  const [evidence, setEvidence] = useState<EvalSourceTimelineItem[] | null>(null);
  const [evidenceErr, setEvidenceErr] = useState(false);
  const [evidenceDone, setEvidenceDone] = useState(false);
  const [evidenceMore, setEvidenceMore] = useState(false);
  const [sessions, setSessions] = useState<EvalSessionItem[] | null>(null);
  const [sessionsErr, setSessionsErr] = useState(false);
  const [sessionsDone, setSessionsDone] = useState(false);
  const [sessionsMore, setSessionsMore] = useState(false);
  const [conceptPage, setConceptPage] = useState(0);
  const [conceptTotal, setConceptTotal] = useState(0);
  const [concepts, setConcepts] = useState<ConceptEvaluationView[] | null>(null);
  const [conceptsErr, setConceptsErr] = useState(false);
  const [tab, setTab] = useState("changes");
  const [stateFilter, setStateFilter] = useState("");
  const [openConcept, setOpenConcept] = useState<string>("");
  const [openSession, setOpenSession] = useState<string>("");
  const [sessionItems, setSessionItems] = useState<EvalSessionEvidenceItem[] | null>(null);

  const CONCEPTS_PER_PAGE = 20;

  useEffect(() => {
    let alive = true;
    getEvalWorkspaces(0, 100)
      .then((r) => {
        if (!alive) return;
        const items = r.items || [];
        setWss(items);
        setWsId((prev) => prev || items[0]?.workspace_id || "");
      })
      .catch(() => {
        if (!alive) return;
        setWss([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 工作区切换时在渲染期重置面板状态（React 官方 derive-state 模式，
  // 避免在 effect 内同步 setState 造成级联渲染）。
  const [prevWsId, setPrevWsId] = useState(wsId);
  if (wsId !== prevWsId) {
    setPrevWsId(wsId);
    setSummary(null);
    setSummaryErr(false);
    setEvidence(null);
    setEvidenceErr(false);
    setEvidenceDone(false);
    setEvidenceMore(false);
    setSessions(null);
    setSessionsErr(false);
    setSessionsDone(false);
    setSessionsMore(false);
    setConcepts(null);
    setConceptsErr(false);
    setConceptPage(0);
    setConceptTotal(0);
    setOpenConcept("");
    setOpenSession("");
    setSessionItems(null);
    setStateFilter("");
  }

  // 概念筛选变化 → 回到第一页（渲染期 derive-state，同上）。
  const [prevStateFilter, setPrevStateFilter] = useState(stateFilter);
  if (stateFilter !== prevStateFilter) {
    setPrevStateFilter(stateFilter);
    if (conceptPage !== 0) setConceptPage(0);
  }

  // ①总览独立加载：失败只影响本卡片（可重试），不掩盖其他区域。
  useEffect(() => {
    if (!wsId) return;
    let alive = true;
    getEvalWorkspace(wsId)
      .then((s) => {
        if (!alive) return;
        setSummary(s);
        setSummaryErr(false);
      })
      .catch(() => alive && setSummaryErr(true));
    return () => {
      alive = false;
    };
  }, [wsId]);

  const loadEvidencePage = useCallback(
    (offset: number) => {
      if (!wsId) return;
      setEvidenceMore(true);
      setEvidenceErr(false);
      getEvalEvidence(wsId, { limit: 30, offset })
        .then((ev) => {
          const items = ev.items || [];
          setEvidence((prev) => (offset === 0 ? items : [...(prev || []), ...items]));
          setEvidenceDone(offset + items.length >= (ev.total || 0));
        })
        .catch(() => {
          setEvidenceErr(true);
          setEvidence((prev) => prev ?? []);
        })
        .finally(() => setEvidenceMore(false));
    },
    [wsId],
  );

  useEffect(() => {
    if (!wsId) return;
    // 重置走微任务：避免 effect 体内同步 setState 的级联渲染
    // （react-hooks/set-state-in-effect）。
    let alive = true;
    queueMicrotask(() => {
      if (!alive) return;
      setEvidence(null);
      setEvidenceDone(false);
      loadEvidencePage(0);
    });
    return () => {
      alive = false;
    };
  }, [wsId, loadEvidencePage]);

  const loadSessionsPage = useCallback(
    (offset: number) => {
      if (!wsId) return;
      setSessionsMore(true);
      setSessionsErr(false);
      getEvalSessions(wsId, offset, 50)
        .then((se) => {
          const items = se.items || [];
          setSessions((prev) => (offset === 0 ? items : [...(prev || []), ...items]));
          setSessionsDone(offset + items.length >= (se.total || 0));
        })
        .catch(() => {
          setSessionsErr(true);
          setSessions((prev) => prev ?? []);
        })
        .finally(() => setSessionsMore(false));
    },
    [wsId],
  );

  useEffect(() => {
    if (!wsId) return;
    // 重置走微任务：避免 effect 体内同步 setState（同 evidence 区）。
    let alive = true;
    queueMicrotask(() => {
      if (!alive) return;
      setSessions(null);
      setSessionsDone(false);
      loadSessionsPage(0);
    });
    return () => {
      alive = false;
    };
  }, [wsId, loadSessionsPage]);

  // ③概念：服务端筛选 + 分页（limit ≤100 统一上限），不拉全量（R09）。
  useEffect(() => {
    if (!wsId) return;
    let alive = true;
    getEvalConcepts(wsId, {
      state: stateFilter || undefined,
      offset: conceptPage * CONCEPTS_PER_PAGE,
      limit: CONCEPTS_PER_PAGE,
    })
      .then((co) => {
        if (!alive) return;
        setConcepts(co.items || []);
        setConceptTotal(co.total || 0);
        setConceptsErr(false);
      })
      .catch(() => alive && setConceptsErr(true));
    return () => {
      alive = false;
    };
  }, [wsId, stateFilter, conceptPage]);

  if (wss === null) {
    return <p className="py-2 text-xs text-muted">{tr("ws.loading")}</p>;
  }
  if (wss.length === 0) {
    return (
      <EmptyState
        icon={<Archive size={20} />}
        title={tr("arc.workspaces.empty")}
        desc={tr("arc.workspaces.empty.desc")}
      />
    );
  }
  const t = (zh: string, en: string) => (lang === "en" ? en : zh);
  const coverage = summary?.coverage;
  const byState = coverage?.by_state || {};
  const STATE_ORDER = ["emerging", "supported_in_scope", "fragile", "conflicting"];
  const probe = summary?.synthesis?.priority_probe || null;

  const startProbe = (p: EvalNextProbe) => {
    router.push(`/chat?q=${encodeURIComponent(p.instruction)}&send=1`);
  };

  return (
    <div className="flex flex-col gap-4" data-testid="learning-archive">
      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <label className="flex items-center gap-2 text-xs text-fg-secondary">
            {t("学习区", "Workspace")}
            <select
              value={wsId}
              onChange={(e) => setWsId(e.target.value)}
              className="h-8 rounded-[7px] border border-border bg-surface px-2 text-xs text-fg"
            >
              {wss.map((w) => (
                <option key={w.workspace_id} value={w.workspace_id}>
                  {w.workspace_name || w.workspace_id}
                </option>
              ))}
            </select>
          </label>
          {summary?.updated_at && (
            <span className="tnum text-[0.66rem] text-muted">
              {tr("arc.updated")} {fmtIso(summary.updated_at)}
            </span>
          )}
        </div>

        {summaryErr ? (
          <ErrorNote message={tr("arc.error")} retry={() => {
            const keep = wsId;
            setWsId("");
            setTimeout(() => setWsId(keep), 0);
          }} />
        ) : summary === null ? (
          <p className="py-2 text-xs text-muted">…</p>
        ) : (
          <>
            {summary.synthesis ? (
              <p className="mb-3 text-xs leading-relaxed text-fg">
                {summary.synthesis.statement}
              </p>
            ) : (
              <p className="mb-3 text-xs leading-relaxed text-muted">
                {tr("arc.synthesis.empty")}
              </p>
            )}
            <div className="mb-3 flex flex-wrap items-center gap-1.5">
              {STATE_ORDER.filter((s) => (byState[s] || 0) > 0).map((s) => (
                <Badge key={s} tone={evalStateTone(s)}>
                  {et(lang, `eval.state.${s}`)} {byState[s]}
                </Badge>
              ))}
              <Badge tone="muted">
                {et(lang, "eval.state.not_observed")} {coverage?.not_observed_concepts ?? 0}
              </Badge>
              <span className="text-[0.66rem] text-muted">{tr("arc.concepts.note")}</span>
            </div>
            <StatusNote
              status={summary.evaluation_status}
              pendingSourceCount={summary.pending_source_count}
              lang={lang}
            />
            {probe && (
              <div className="mt-2">
                <p className="mb-1 text-[0.7rem] font-medium text-fg-secondary">
                  {tr("arc.next")}
                </p>
                <NextProbeAction
                  probe={probe}
                  lang={lang}
                  actions={
                    <button
                      type="button"
                      onClick={() => startProbe(probe)}
                      className="cursor-pointer rounded-[7px] bg-accent px-2.5 py-1 text-xs font-medium text-white hover:bg-accent-strong"
                    >
                      {tr("arc.startProbe")}
                    </button>
                  }
                />
              </div>
            )}
            {summary.synthesis?.limits?.length ? (
              <ul className="mt-2 space-y-0.5">
                {summary.synthesis.limits.map((l, i) => (
                  <li key={i} className="text-[0.7rem] leading-relaxed text-muted">· {l}</li>
                ))}
              </ul>
            ) : null}
          </>
        )}
      </Card>

      <Tabs
        active={tab}
        onChange={setTab}
        items={[
          { key: "changes", label: tr("arc.tab.changes") },
          { key: "sessions", label: tr("arc.tab.sessions") },
          { key: "concepts", label: tr("arc.tab.concepts") },
        ]}
      />

      {tab === "changes" &&
        (evidence === null ? (
          <p className="py-2 text-xs text-muted">…</p>
        ) : (
          <div className="flex flex-col gap-2">
            <EvidenceTimeline items={evidence} lang={lang} />
            {evidenceErr && (
              <ErrorNote
                message={tr("arc.error")}
                retry={() => loadEvidencePage(evidence.length)}
              />
            )}
            {!evidenceDone && !evidenceMore && !evidenceErr && evidence.length > 0 && (
              <button
                type="button"
                onClick={() => loadEvidencePage(evidence.length)}
                className="mx-auto cursor-pointer rounded-[7px] border border-border px-3 py-1 text-xs text-fg-secondary hover:border-accent"
              >
                {tr("arc.loadMore")}
              </button>
            )}
            {evidenceMore && <p className="py-1 text-center text-xs text-muted">…</p>}
          </div>
        ))}

      {tab === "sessions" && (
        <Card>
          {sessions === null ? (
            <p className="py-2 text-xs text-muted">…</p>
          ) : sessionsErr && sessions.length === 0 ? (
            <ErrorNote message={tr("arc.error")} retry={() => loadSessionsPage(0)} />
          ) : sessions.length === 0 ? (
            <EmptyState
              title={tr("arc.session.noEvidence")}
              desc={tr("arc.workspaces.empty.desc")}
            />
          ) : (
            <>
              <ul className="divide-y divide-border-light">
                {sessions.map((s) => {
                  const isOpen = openSession === s.source_session_ref;
                  const chatAlive = s.availability === "available";
                  const independent = s.kinds.length > 0 && s.kinds.every((k) => k === "assessment");
                  return (
                    <li key={s.source_session_ref}>
                      <button
                        type="button"
                        onClick={() => {
                          setOpenSession(isOpen ? "" : s.source_session_ref);
                          if (!isOpen) {
                            setSessionItems(null);
                            getEvalSessionEvidence(wsId, s.source_session_ref)
                              .then((r) => setSessionItems(r.items || []))
                              .catch(() => setSessionItems([]));
                          }
                        }}
                        className="flex w-full cursor-pointer items-center gap-2 py-2 text-left"
                      >
                        {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">
                          {independent ? tr("arc.session.independent") : s.source_session_ref}
                        </span>
                        <Badge tone={s.has_evidence ? "accent" : "outline"}>
                          {s.has_evidence ? tr("arc.session.evidence") : tr("arc.session.noEvidence")}
                        </Badge>
                        {s.availability !== "available" && (
                          <Badge tone="muted">
                            {s.availability === "deleted"
                              ? tr("arc.session.deleted")
                              : et(lang, `eval.avail.${s.availability}`)}
                          </Badge>
                        )}
                        <span className="tnum shrink-0 text-[0.66rem] text-muted">
                          {fmtIso(s.last_observed_at)}
                        </span>
                      </button>
                      {isOpen && (
                        <div className="space-y-1.5 pb-2.5 pl-5">
                          {sessionItems === null ? (
                            <p className="text-[0.72rem] text-muted">…</p>
                          ) : sessionItems.length === 0 ? (
                            <p className="text-[0.72rem] text-muted">{tr("arc.session.noEvidence")}</p>
                          ) : (
                            sessionItems.map((it) => (
                              <div
                                key={it.source_id}
                                className="rounded-[8px] border border-border-light bg-surface px-2.5 py-2"
                              >
                                <div className="mb-1 flex items-center gap-1.5">
                                  <Badge tone={it.kind === "dialogue" ? "info" : "accent"}>
                                    {et(lang, `eval.source.${it.kind || "dialogue"}`)}
                                  </Badge>
                                  <span className="tnum text-[0.66rem] text-muted">
                                    {fmtIso(it.observed_at)}
                                  </span>
                                </div>
                                <p className="line-clamp-2 text-[0.72rem] leading-relaxed text-fg-secondary">
                                  {it.canonical_text}
                                </p>
                              </div>
                            ))
                          )}
                          {/* §11.2：删除对话后不得把已删除 ID 编进聊天深链 */}
                          {chatAlive && !independent && (
                            <Link
                              href={`/chat/${encodeURIComponent(s.source_session_ref)}`}
                              className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                            >
                              <MessagesSquare size={12} />
                              {tr("arc.session.back")}
                            </Link>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
              {!sessionsDone && !sessionsMore && (
                <button
                  type="button"
                  onClick={() => loadSessionsPage(sessions.length)}
                  className="mt-2 cursor-pointer rounded-[7px] border border-border px-3 py-1 text-xs text-fg-secondary hover:border-accent"
                >
                  {tr("arc.loadMore")}
                </button>
              )}
              {sessionsMore && <p className="py-1 text-center text-xs text-muted">…</p>}
            </>
          )}
        </Card>
      )}

      {tab === "concepts" && (
        <Card>
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setStateFilter("")}
              className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs transition-colors ${
                stateFilter === ""
                  ? "border-accent bg-accent-soft text-accent-strong"
                  : "border-border text-fg-secondary hover:border-accent"
              }`}
            >
              {tr("arc.filter.all")}{" "}
              {coverage ? coverage.observed_concepts + coverage.not_observed_concepts : ""}
            </button>
            {STATE_ORDER.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setStateFilter(stateFilter === s ? "" : s)}
                className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs transition-colors ${
                  stateFilter === s
                    ? "border-accent bg-accent-soft text-accent-strong"
                    : "border-border text-fg-secondary hover:border-accent"
                }`}
              >
                {et(lang, `eval.state.${s}`)} {byState[s] || 0}
              </button>
            ))}
          </div>
          {conceptsErr ? (
            <ErrorNote
              message={tr("arc.error")}
              retry={() => {
                const p = conceptPage;
                setConceptPage(-1);
                setTimeout(() => setConceptPage(p), 0);
              }}
            />
          ) : concepts === null ? (
            <p className="py-2 text-xs text-muted">…</p>
          ) : concepts.length === 0 ? (
            <EmptyState title={tr("arc.filter.empty")} desc={tr("arc.concepts.note")} />
          ) : (
            <>
              <ul className="divide-y divide-border-light">
                {concepts.map((c) => {
                  const key = c.concept_ref.key || c.concept_ref.concept_id;
                  const isOpen = openConcept === key;
                  return (
                    <li key={key} className="py-1">
                      <button
                        type="button"
                        onClick={() => setOpenConcept(isOpen ? "" : key)}
                        aria-expanded={isOpen}
                        className="flex w-full cursor-pointer items-center gap-2 py-1 text-left"
                      >
                        {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        <span className="min-w-0 flex-1 truncate text-xs text-fg">
                          {c.concept_ref.display_name || c.concept_ref.concept_id}
                        </span>
                        <Badge tone={evalStateTone(c.state || "not_observed")}>
                          {et(lang, `eval.state.${c.state || "not_observed"}`)}
                        </Badge>
                      </button>
                      {isOpen && (
                        <div className="pb-2 pl-5">
                          <SemanticEvaluationPanel
                            view={c}
                            lang={lang}
                            workspaceId={wsId}
                            onStartProbe={startProbe}
                            onExplain={() =>
                              router.push(
                                `/knowledge?concept=${encodeURIComponent(c.concept_ref.concept_id)}&workspace=${encodeURIComponent(wsId)}`,
                              )
                            }
                          />
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
              <Pager
                page={conceptPage}
                total={conceptTotal}
                per={CONCEPTS_PER_PAGE}
                onPage={setConceptPage}
              />
            </>
          )}
        </Card>
      )}
    </div>
  );
}

export default function MemoryPage() {
  const lang = useUIStore((s) => s.lang);
  const tr = makePageT(lang, STRINGS);

  const [region, setRegion] = useState<"archive" | "ai">("archive");
  const [tab, setTab] = useState("procedural");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [episodes, setEpisodes] = useState<EpisodesState>({ status: "ok", list: [], hasMore: false });
  const [facts, setFacts] = useState<{ status: string; list: SemanticFact[] }>({ status: "ok", list: [] });
  const [strategies, setStrategies] = useState<{ status: string; list: ProceduralStrategy[] }>({
    status: "ok",
    list: [],
  });
  const [loadingMore, setLoadingMore] = useState(false);
  const [profile, setProfile] = useState<PromptMemoryProfile | null>(null);
  const [sessionTitles, setSessionTitles] = useState<Map<string, string>>(new Map());
  const [promptWindow, setPromptWindow] = useState(15);

  const load = useCallback(async () => {
    try {
      const [ep, sem, pro, pf, sessions] = await Promise.all([
        getEpisodes(PAGE_SIZE),
        getSemanticMemory(),
        getProceduralMemory(),
        getPromptMemoryProfile(),
        listSessions().catch(() => ({ sessions: [] })),
      ]);
      setEpisodes({ status: ep.status, list: ep.episodes ?? [], hasMore: ep.has_more ?? false });
      setFacts({ status: sem.status, list: sem.facts ?? [] });
      setStrategies({ status: pro.status, list: pro.strategies ?? [] });
      setProfile(pf);
      setPromptWindow(pf.window_size);
      const titles = new Map<string, string>();
      for (const s of sessions.sessions ?? []) titles.set(s.session_id, s.title);
      setSessionTitles(titles);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // 延迟到微任务触发，避免在 effect 体内同步 setState
    // （react-hooks/set-state-in-effect）。不用 rAF：后台标签页/不可见
    // webview 不绘制帧时 rAF 永不触发，区域会永远停在骨架屏。
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (!cancelled) load();
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const retry = useCallback(() => {
    setLoading(true);
    setError(false);
    load();
  }, [load]);

  const changeWindow = useCallback((n: number) => {
    setPromptWindow(n);
  }, []);

  const commitWindow = useCallback(() => {
    void setPromptMemoryWindow(promptWindow).then((p) => setPromptWindow(p.window_size));
  }, [promptWindow]);

  const loadMore = useCallback(async () => {
    const last = episodes.list[episodes.list.length - 1];
    if (!last || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await getEpisodes(PAGE_SIZE, last.ts);
      if (res.status === "ok") {
        setEpisodes((prev) => ({
          status: res.status,
          list: [...prev.list, ...(res.episodes ?? [])],
          hasMore: res.has_more ?? false,
        }));
      }
    } catch {
      /* 保持已有数据，用户可再次点击重试 */
    } finally {
      setLoadingMore(false);
    }
  }, [episodes.list, loadingMore]);

  const disabledNote = <EmptyState title={tr("mem.disabled")} />;
  const auditCount = episodes.list.length + facts.list.length;
  // 历史审计只承载旧版系统留存数据：两接口都已加载完且总数为 0 时整块
  // 隐藏（加载中不算空，避免闪隐）；新用户不再看到空 tab。
  const auditEmpty =
    episodes.status === "ok" && facts.status === "ok" && auditCount === 0;
  // 数据重载后变空时，已停留在 audit 的状态回落到 procedural
  const activeTab = tab === "audit" && auditEmpty ? "procedural" : tab;

  return (
    <div className="h-full overflow-y-auto p-6 page-in">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
        <header>
          <h1 className="font-serif text-xl font-bold text-fg">{tr("nav.memory")}</h1>
          <p className="mt-1 text-sm text-muted">{tr("mem.desc")}</p>
        </header>

        <Tabs
          active={region}
          onChange={(k) => setRegion(k as "archive" | "ai")}
          items={[
            { key: "archive", label: tr("mem.region.archive") },
            { key: "ai", label: tr("mem.region.ai") },
          ]}
        />

        {region === "archive" ? (
          <LearningArchiveRegion tr={tr} lang={lang} />
        ) : loading ? (
          <PageSkeleton />
        ) : error ? (
          <ErrorNote message={tr("mem.error")} retry={retry} />
        ) : (
          <>
            <PromptMemorySection
              profile={profile}
              sessionTitles={sessionTitles}
              windowValue={promptWindow}
              maxWindow={profile?.max_window ?? 30}
              onWindowChange={changeWindow}
              onWindowBlur={commitWindow}
              tr={tr}
              lang={lang}
            />

            <WorkspaceMemorySection tr={tr} lang={lang} />

            <Tabs
              active={activeTab}
              onChange={setTab}
              items={[
                {
                  key: "procedural",
                  label: tr("mem.tab.procedural"),
                  badge: strategies.status === "ok" && <Badge tone="muted">{strategies.list.length}</Badge>,
                },
                ...(!auditEmpty
                  ? [
                      {
                        key: "audit",
                        label: tr("mem.tab.audit"),
                        badge: <Badge tone="muted">{auditCount}</Badge>,
                      },
                    ]
                  : []),
              ]}
            />

            {activeTab === "procedural" &&
              (strategies.status === "disabled" ? (
                disabledNote
              ) : strategies.status !== "ok" ? (
                <ErrorNote message={tr("mem.error")} retry={retry} />
              ) : (
                <Card>
                  <StrategyBars strategies={strategies.list} lang={lang} tr={tr} />
                </Card>
              ))}

            {activeTab === "audit" && (
              <>
                <Card className="border-info/30 bg-info/8 py-3">
                  <div className="flex items-start gap-2 text-xs leading-relaxed text-info">
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>{tr("audit.note")}</span>
                  </div>
                </Card>
                {episodes.status === "disabled" || facts.status === "disabled" ? (
                  disabledNote
                ) : (
                  <>
                    <EpisodeTimeline
                      episodes={episodes.list}
                      hasMore={episodes.hasMore}
                      loadingMore={loadingMore}
                      onLoadMore={loadMore}
                      lang={lang}
                      tr={tr}
                    />
                    <SemanticFacts facts={facts.list} tr={tr} />
                  </>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
