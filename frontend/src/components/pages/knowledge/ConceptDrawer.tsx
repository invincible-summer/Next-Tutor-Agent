"use client";
// 概念详情抽屉：getConceptDetail(id) 的五组邻边 / 掌握度 / 教学记录 / 记忆。
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ClipboardList, MessageSquare, Target } from "lucide-react";
import { Drawer } from "@/components/ui/Drawer";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton, ErrorNote, EmptyState } from "@/components/ui/EmptyState";
import { MasteryRing } from "@/components/charts/MasteryRing";
import { getConceptDetail, getEvidenceProfile } from "@/lib/api-modules";
import type { ConceptDetailResp, Episode, EvidenceConcept, KnowledgeNode, TeachingLogEntry } from "@/lib/types-modules";
import { dt, modeTone, stateTone } from "@/lib/labels";
import { relTime } from "@/lib/format";
import type { Lang } from "@/lib/i18n";

type Tr = (key: string, fallback?: string) => string;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">{title}</div>
      {children}
    </section>
  );
}

const EVIDENCE_DIMENSIONS: { key: string; labelKey: string; fallback: string }[] = [
  { key: "concept", labelKey: "evidence.dim.concept", fallback: "概念理解" },
  { key: "procedure", labelKey: "evidence.dim.procedure", fallback: "过程操作" },
  { key: "reasoning", labelKey: "evidence.dim.reasoning", fallback: "推理解释" },
  { key: "transfer", labelKey: "evidence.dim.transfer", fallback: "迁移应用" },
  { key: "retention", labelKey: "evidence.dim.retention", fallback: "延迟保持" },
  { key: "self_check", labelKey: "evidence.dim.self_check", fallback: "自我检查" },
];

const EVIDENCE_STATUS_TONE: Record<string, "success" | "accent" | "warning" | "outline"> = {
  demonstrated_in_scope: "success",
  developing: "accent",
  needs_recheck: "warning",
  not_observed: "outline",
};

/** W3/F05 依据区：已能做什么 / 哪份作答支持 / 哪些条件未测（v2 证据投影，
 *  读时派生）。无证据的概念不渲染——没有证据不宣称会。 */
function EvidenceSection({
  conceptName,
  lang,
  tr,
  onDispute,
}: {
  conceptName: string;
  lang: Lang;
  tr: Tr;
  onDispute: () => void;
}) {
  // 一次异步装填；setState 只发生在 promise 回调里（不在 effect 体内同步调用）。
  const [loaded, setLoaded] = useState<{ for: string; profile: EvidenceConcept | null } | null>(null);
  const load = useCallback(() => {
    if (!conceptName) return;
    getEvidenceProfile(conceptName)
      .then((res) => {
        setLoaded({
          for: conceptName,
          profile: res.status === "ok" ? res.concepts[0] ?? null : null,
        });
      })
      .catch(() => { /* 无证据/失败 → 不渲染 */ });
  }, [conceptName]);
  useEffect(() => {
    load();
  }, [load]);
  const profile = loaded?.for === conceptName ? loaded.profile : null;
  if (!profile) return null;
  const dim = (k: string) => profile.dimensions?.[k];
  return (
    <Section title={dt(lang, "evidence.title", "依据（能力证据）")}>
      <div className="flex flex-wrap gap-1.5">
        {EVIDENCE_DIMENSIONS.map(({ key, labelKey, fallback }) => {
          const d = dim(key);
          if (!d || d.status === "not_observed") {
            return (
              <span key={key} className="rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] text-muted/70">
                {dt(lang, labelKey, fallback)} · {dt(lang, "evidence.status.not_observed", "未测")}
              </span>
            );
          }
          return (
            <Badge key={key} tone={EVIDENCE_STATUS_TONE[d.status] ?? "outline"} dot>
              {dt(lang, labelKey, fallback)} · {dt(lang, `evidence.status.${d.status}`, d.status)}
            </Badge>
          );
        })}
      </div>
      <div className="mt-2 flex flex-col gap-1">
        <div className="text-[11px] text-muted">
          {tr("attemptsTxt")} {profile.evidence_count} · {dt(lang, "evidence.independent", "独立作答")} {profile.independent_count}
        </div>
        {profile.evidence.slice(0, 3).map((ev, i) => (
          <div key={i} className="flex items-center gap-1.5 text-[11px] text-fg-secondary">
            <Badge tone={ev.verdict === "correct" ? "success" : ev.verdict === "partial" ? "warning" : "danger"}>
              {ev.verdict}
            </Badge>
            {ev.assistance && <span className="text-muted">（{dt(lang, "evidence.assisted", "求助后完成")}）</span>}
          </div>
        ))}
        <button onClick={onDispute} className="mt-1 w-fit text-[11px] text-accent transition-colors hover:text-accent-strong">
          {dt(lang, "evidence.dispute", "对判定有异议？到测评中心重练该题")}
        </button>
      </div>
    </Section>
  );
}

/** 邻边 chips 组：点击切换选中概念。 */
function EdgeGroup({
  label,
  refs,
  onNavigate,
}: {
  label: string;
  refs: KnowledgeNode[];
  onNavigate: (id: string) => void;
}) {
  if (!refs || refs.length === 0) return null;
  return (
    <div>
      <div className="mb-1 text-[11px] font-medium text-muted">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {refs.map((r) => (
          <button
            key={r.id}
            onClick={() => onNavigate(r.id)}
            className="cursor-pointer rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] text-fg-secondary transition-colors hover:border-accent hover:text-accent"
          >
            {r.name || r.id}
          </button>
        ))}
      </div>
    </div>
  );
}

/** content 字段渲染：字符串 → 正文；字符串数组 → 要点列表；其余 JSON → prettify。 */
function ContentView({ content }: { content: unknown }) {
  if (content == null) return null;
  if (typeof content === "string") {
    return <p className="whitespace-pre-wrap text-xs leading-relaxed text-fg-secondary">{content}</p>;
  }
  if (Array.isArray(content) && content.every((x) => typeof x === "string")) {
    return (
      <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-fg-secondary">
        {(content as string[]).map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    );
  }
  return (
    <pre className="overflow-x-auto rounded-[6px] bg-surface-sunken p-2 font-mono text-[11px] leading-relaxed text-fg-secondary">
      {JSON.stringify(content, null, 2)}
    </pre>
  );
}

export function ConceptDrawer({
  id,
  lang,
  tr,
  goal,
  onClose,
  onNavigate,
}: {
  id: string | null;
  lang: Lang;
  tr: Tr;
  /** L1 目标链归属（可空）：属于目标链/目标概念时展示归属行。 */
  goal?: {
    title: string;
    layer: number;
    remaining: number | null;
    isTarget: boolean;
  } | null;
  onClose: () => void;
  onNavigate: (id: string) => void;
}) {
  const router = useRouter();
  const [data, setData] = useState<ConceptDetailResp | null>(null);
  const [err, setErr] = useState(false);
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const reqToken = useRef(0);

  // 派生 loading：当前 id 尚未有响应。避免在 effect 体内同步 setState。
  const loading = !!id && loadedId !== id;

  const load = useCallback(() => {
    if (!id) return;
    const token = ++reqToken.current;
    getConceptDetail(id)
      .then((r) => {
        if (reqToken.current !== token) return;
        setData(r);
        setErr(false);
        setLoadedId(id);
      })
      .catch(() => {
        if (reqToken.current !== token) return;
        setErr(true);
        setLoadedId(id);
      });
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const retry = useCallback(() => {
    setErr(false);
    setLoadedId(null);
    load();
  }, [load]);

  const concept = data?.concept ?? null;
  const mastery = data?.mastery ?? null;
  const log: TeachingLogEntry[] = (data?.teaching_log ?? []).slice(-5).reverse();
  const memories: Episode[] = (data?.memories ?? []).slice(-5).reverse();
  const notFound = data != null && (data.status === "not_found" || !concept);

  // 课文(section)/单元(chapter)节点：渲染结构面板（所属/页码/包含/结构数据），
  // 跳过概念向的掌握度与讲解内容——否则只剩空 JSON stub 一片空白
  const isStruct = concept?.kind === "section" || concept?.kind === "chapter";
  const meta = concept?.metadata ?? null;
  const pageRange = Array.isArray(meta?.page_range)
    ? (meta.page_range as unknown[]).filter((x): x is number => typeof x === "number")
    : [];
  const pagesLabel = pageRange.length >= 2
    ? `${pageRange[0]}–${pageRange[pageRange.length - 1]}`
    : pageRange.length === 1 ? `${pageRange[0]}` : null;

  // 双 CTA：带上下文 auto-send 直达对话（/chat?q=...&send=1）。
  const openChat = useCallback(
    (msg: string) => router.push(`/chat?q=${encodeURIComponent(msg)}&send=1`),
    [router],
  );
  const learnMsg = useCallback(() => {
    if (!concept) return "";
    const meta =
      (concept.subject ? `${concept.subject}·` : "") +
      `${tr("difficulty")}${concept.difficulty}` +
      (mastery ? tr("drawer.mastery.part").replace("%s", dt(lang, `state.${mastery.state}`)) : "");
    return tr("drawer.learn.msg").replace("%c", concept.name).replace("%m", meta);
  }, [concept, mastery, tr, lang]);
  const quizMsg = useCallback(
    () => (concept ? tr("drawer.quiz.msg").replace("%c", concept.name) : ""),
    [concept, tr],
  );

  return (
    <Drawer open={!!id} onClose={onClose} title={concept?.name ?? id ?? ""} width={440}>
      {err && <ErrorNote message={tr("loadFail")} retry={retry} />}
      {!err && notFound && <EmptyState title={tr("notFound")} />}
      {!err && !notFound && loading && !data && (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-16" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      )}
      {!err && concept && (
        <div className="flex flex-col gap-4">
          {/* 头部：学科 / 难度 / 状态 */}
          <div className="flex flex-wrap items-center gap-2">
            {concept.subject && <Badge tone="accent">{concept.subject}</Badge>}
            <Badge tone="outline">
              {tr("difficulty")} <span className="tnum">{concept.difficulty}</span>
            </Badge>
            {mastery && (
              <Badge tone={stateTone(mastery.state)} dot>
                {dt(lang, `state.${mastery.state}`)}
              </Badge>
            )}
          </div>

          {/* L1 目标链归属：属于目标/目标链时展示（编排页 ↔ 谱系页双向导航的一端） */}
          {goal && (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-[8px] border border-accent/30 bg-accent-soft/40 px-2.5 py-1.5 text-[0.72rem] text-accent-strong">
              <Target size={12} className="shrink-0" />
              <span className="font-medium">
                {tr("drawer.goal", "属于目标").replace("%t", goal.title)}
              </span>
              {goal.isTarget && (
                <Badge tone="accent">{tr("drawer.goal.target", "目标概念")}</Badge>
              )}
              {goal.remaining != null && goal.remaining > 0 && (
                <span className="tnum">
                  {tr("drawer.goal.remaining", "距目标还差 %n 个概念").replace("%n", String(goal.remaining))}
                </span>
              )}
              {goal.layer > 0 && (
                <span className="tnum">
                  {tr("drawer.goal.layer", "当前第 %n 层").replace("%n", String(goal.layer))}
                </span>
              )}
            </div>
          )}

          {/* 对话联动：带着概念上下文 auto-send 直达 /chat */}
          <div className="flex gap-2">
            <Button size="sm" className="flex-1" icon={<MessageSquare size={13} />}
              onClick={() => openChat(learnMsg())}>
              {tr("drawer.learn")}
            </Button>
            <Button size="sm" variant="outline" className="flex-1" icon={<ClipboardList size={13} />}
              onClick={() => openChat(quizMsg())}>
              {tr("drawer.quiz")}
            </Button>
          </div>

          {concept.description && (
            <p className="text-xs leading-relaxed text-fg-secondary">{concept.description}</p>
          )}

          {concept.aliases && concept.aliases.length > 0 && (
            <Section title={tr("aliases")}>
              <div className="flex flex-wrap gap-1.5">
                {concept.aliases.map((a) => (
                  <Badge key={a} tone="outline">
                    {a}
                  </Badge>
                ))}
              </div>
            </Section>
          )}

          {/* 课文/单元结构面板：所属 / 教材页码 / 包含 / 结构数据 JSON */}
          {isStruct && (
            <div className="flex flex-col gap-3 rounded-[10px] border border-border bg-surface-sunken/40 p-3">
              <EdgeGroup label={tr("drawer.parents")} refs={data?.edges?.parents ?? []} onNavigate={onNavigate} />
              {pagesLabel && (
                <div>
                  <div className="mb-1 text-[11px] font-medium text-muted">{tr("drawer.pages")}</div>
                  <span className="tnum text-xs text-fg-secondary">{pagesLabel}</span>
                </div>
              )}
              <EdgeGroup label={tr("drawer.children")} refs={data?.edges?.children ?? []} onNavigate={onNavigate} />
              {meta != null && Object.keys(meta).length > 0 && (
                <div>
                  <div className="mb-1 text-[11px] font-medium text-muted">{tr("drawer.struct")}</div>
                  <ContentView content={meta} />
                </div>
              )}
            </div>
          )}

          {mastery && (
            <Section title={tr("masteryTitle")}>
              <div className="flex items-center gap-3">
                <MasteryRing value={mastery.p_known} size={64} />
                <div className="text-xs text-muted">
                  {(mastery.attempts ?? 0) > 0 && (
                    <div className="tnum">
                      {mastery.attempts} {tr("attemptsTxt")} · {mastery.correct ?? 0} {tr("correctTxt")}
                    </div>
                  )}
                </div>
              </div>
            </Section>
          )}

          {concept?.name && (
            <EvidenceSection
              conceptName={concept.name}
              lang={lang}
              tr={tr}
              onDispute={() => router.push("/assessment")}
            />
          )}

          {concept.common_errors && concept.common_errors.length > 0 && (
            <Section title={tr("commonErrors")}>
              <ul className="flex flex-col gap-1.5">
                {concept.common_errors.map((e, i) => (
                  <li key={i} className="flex items-start gap-1.5 text-xs leading-relaxed text-fg-secondary">
                    <AlertTriangle size={13} className="mt-0.5 shrink-0 text-warning" />
                    <span>{e}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {!isStruct && concept.content != null && (
            <Section title={tr("contentTitle")}>
              <ContentView content={concept.content} />
            </Section>
          )}

          {data?.edges && (
            <div className="flex flex-col gap-3 border-t border-border-light pt-3">
              {!isStruct && (
                <EdgeGroup label={tr("drawer.parents")} refs={data.edges.parents} onNavigate={onNavigate} />
              )}
              <EdgeGroup label={dt(lang, "edge.prerequisite")} refs={data.edges.prerequisites} onNavigate={onNavigate} />
              <EdgeGroup label={tr("unlocks")} refs={data.edges.unlocks} onNavigate={onNavigate} />
              <EdgeGroup label={dt(lang, "edge.related")} refs={data.edges.related} onNavigate={onNavigate} />
              <EdgeGroup label={dt(lang, "edge.application")} refs={data.edges.applications} onNavigate={onNavigate} />
              <EdgeGroup label={dt(lang, "edge.misconception")} refs={data.edges.misconceptions} onNavigate={onNavigate} />
            </div>
          )}

          {log.length > 0 && (
            <Section title={tr("teachingLog")}>
              <ul className="flex flex-col gap-2">
                {log.map((e, i) => (
                  <li key={i} className="flex items-center gap-2 text-xs">
                    <Badge tone={modeTone(e.mode)}>{dt(lang, `mode.${e.mode}`, e.mode)}</Badge>
                    <span className="min-w-0 flex-1 truncate text-fg-secondary">{e.note}</span>
                    <span className="shrink-0 text-muted">{relTime(e.ts, lang)}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {memories.length > 0 && (
            <Section title={tr("memories")}>
              <ul className="flex flex-col gap-2">
                {memories.map((m) => (
                  <li key={m.id} className="flex items-start justify-between gap-2 text-xs">
                    <span className="min-w-0 flex-1 leading-relaxed text-fg-secondary">{m.summary}</span>
                    <span className="shrink-0 text-muted">{relTime(m.ts, lang)}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}
    </Drawer>
  );
}
