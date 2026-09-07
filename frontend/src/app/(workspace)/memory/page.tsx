"use client";
// /memory 记忆中心 = 记忆总览（L3 呈现层）：
// ①提示词记忆（用户级永久记忆：AI 跨对话记住什么，含窗口/压缩审计）
// ②工作区共同记忆（同工作区对话共享的学习情况摘要）
// ③学习内容档案（不注入对话的业务档案入口：学习账本/教学档案/编排档案）
// ④Tabs：程序性记忆（活数据）+ 历史审计（旧版情景/语义只读陈列，C4 合并）。
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Archive,
  BookOpen,
  ChevronDown,
  ChevronRight,
  FileClock,
  Info,
  Layers,
  MessagesSquare,
  Users,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { Tabs } from "@/components/ui/Tabs";
import { EmptyState, ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { EpisodeTimeline } from "@/components/pages/memory/EpisodeTimeline";
import { SemanticFacts } from "@/components/pages/memory/SemanticFacts";
import { StrategyBars } from "@/components/pages/memory/StrategyBars";
import { getEpisodes, getLearningRecords, getProceduralMemory, getSemanticMemory } from "@/lib/api-modules";
import {
  getPromptMemoryProfile,
  getWorkspace,
  listSessions,
  listWorkspaces,
  setPromptMemoryWindow,
} from "@/lib/api";
import { makePageT } from "@/lib/i18n-page";
import { useUIStore } from "@/lib/store";
import { fmtDate, relTime } from "@/lib/format";
import type {
  Episode,
  LearningRecordItem,
  ProceduralStrategy,
  SemanticFact,
} from "@/lib/types-modules";
import type { PromptMemoryProfile, WorkspaceItem } from "@/lib/types";
import { STRINGS } from "./strings";

const PAGE_SIZE = 100;

interface EpisodesState {
  status: string;
  list: Episode[];
  hasMore: boolean;
}

/** ①提示词记忆区：core_profile 四字段 + 注入字符 + 窗口会话清单 + 压缩审计。 */
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
    ["current_level", tr("pm.field.current_level")],
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

/** ②工作区共同记忆区：各工作区展开查看共享学习摘要（7 字段 LLM 结构化文本）。 */
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

/** ③学习内容档案区：不注入对话提示词的业务档案入口（明示语义边界）。 */
function LearningArchiveSection({
  tr,
  lang,
}: {
  tr: (key: string, fallback?: string) => string;
  lang: string;
}) {
  const [records, setRecords] = useState<LearningRecordItem[] | null>(null);
  useEffect(() => {
    let alive = true;
    getLearningRecords(8)
      .then((r) => alive && setRecords(r.status === "ok" ? r.items : []))
      .catch(() => alive && setRecords([]));
    return () => {
      alive = false;
    };
  }, []);
  const verdictOf = (v: string) =>
    v === "correct" ? tr("ar.correct") : v === "partial" ? tr("ar.partial") : v === "wrong" ? tr("ar.wrong") : tr("ar.ungraded");
  const toneOf = (v: string): "success" | "warning" | "danger" | "muted" =>
    v === "correct" ? "success" : v === "partial" ? "warning" : v === "wrong" ? "danger" : "muted";

  return (
    <Card>
      <CardHeader
        icon={<Archive size={16} className="text-accent" />}
        title={tr("ar.title")}
        desc={tr("ar.desc")}
      />
      <p className="mb-3 rounded-[8px] bg-surface-hover/50 px-3 py-2 text-[0.72rem] leading-relaxed text-fg-secondary">
        {tr("ar.boundary")}
      </p>
      <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Link href="/knowledge"
          className="flex items-center gap-2 rounded-[8px] border border-border-light bg-surface px-3 py-2 text-xs text-fg-secondary transition-colors hover:border-accent hover:text-accent">
          <BookOpen size={13} />
          {tr("ar.teaching")}
        </Link>
        <Link href="/orchestration"
          className="flex items-center gap-2 rounded-[8px] border border-border-light bg-surface px-3 py-2 text-xs text-fg-secondary transition-colors hover:border-accent hover:text-accent">
          <MessagesSquare size={13} />
          {tr("ar.orchestration")}
        </Link>
      </div>
      <p className="mb-1 flex items-center gap-1.5 text-[0.7rem] font-medium text-fg-secondary">
        <FileClock size={12} />
        {tr("ar.recent")}
      </p>
      {records === null ? (
        <p className="py-1 text-xs text-muted">{tr("ws.loading")}</p>
      ) : records.length === 0 ? (
        <p className="py-1 text-[0.72rem] text-muted">{tr("ar.recent.empty")}</p>
      ) : (
        <ul className="divide-y divide-border-light">
          {records.map((r) => (
            <li key={r.record_id} className="flex items-center gap-2 py-1.5">
              <Badge tone={toneOf(r.verdict)}>{verdictOf(r.verdict)}</Badge>
              <span className="min-w-0 flex-1 truncate text-xs text-fg">
                {r.knowledge_point || r.stem}
              </span>
              <span className="tnum shrink-0 text-[0.66rem] text-muted">
                {new Date(r.updated_at * 1000).toLocaleDateString(lang === "en" ? "en" : "zh")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function MemoryPage() {
  const lang = useUIStore((s) => s.lang);
  const tr = makePageT(lang, STRINGS);

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
    // 延迟到 rAF 回调中触发，避免在 effect 体内同步 setState
    // （react-hooks/set-state-in-effect），与 Sidebar 的既有模式一致。
    const id = requestAnimationFrame(() => {
      load();
    });
    return () => cancelAnimationFrame(id);
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

        {loading ? (
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

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <WorkspaceMemorySection tr={tr} lang={lang} />
              <LearningArchiveSection tr={tr} lang={lang} />
            </div>

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
