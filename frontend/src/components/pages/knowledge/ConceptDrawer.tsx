"use client";
// 概念详情抽屉：getConceptDetail(id) 的五组邻边 + 统一评价面板（§14.3：
// 同一 SemanticEvaluationPanel 供 Memory/ConceptDrawer 复用；judgment_id
// 与状态映射全站一致）/ 教学记录 / 记忆。
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ClipboardList, MessageSquare, Target } from "lucide-react";
import { Drawer } from "@/components/ui/Drawer";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton, ErrorNote, EmptyState } from "@/components/ui/EmptyState";
import { SemanticEvaluationPanel } from "@/components/learning-evaluation/SemanticEvaluationPanel";
import { getConceptDetail, getEvalConceptDetail } from "@/lib/api-modules";
import type {
  ConceptDetailResp,
  ConceptEvaluationView,
  Episode,
  KnowledgeNode,
  TeachingLogEntry,
} from "@/lib/types-modules";
import { dt, modeTone } from "@/lib/labels";
import { et, evalStateTone } from "@/lib/evaluation-labels";
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
  workspaceId = "",
  onClose,
  onNavigate,
}: {
  id: string | null;
  lang: Lang;
  tr: Tr;
  /** 当前工作区（§11.6/§14.4）：空 = 仅浏览教材，无个人评价面板。 */
  workspaceId?: string;
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
  const [evalView, setEvalView] = useState<ConceptEvaluationView | null>(null);
  const [err, setErr] = useState(false);
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const reqToken = useRef(0);

  // 派生 loading：当前 id 尚未有响应。避免在 effect 体内同步 setState。
  const loading = !!id && loadedId !== id;

  const load = useCallback(() => {
    if (!id) return;
    const token = ++reqToken.current;
    getConceptDetail(id, "student_default", workspaceId)
      .then((r) => {
        if (reqToken.current !== token) return;
        setData(r);
        setErr(false);
        setLoadedId(id);
        // 完整概念视图（主张/变化/next_probe）：有区 + 有 overlay key 时加载。
        const key = r?.evaluation?.concept_key;
        if (workspaceId && key) {
          getEvalConceptDetail(workspaceId, key)
            .then((v) => {
              if (reqToken.current !== token) return;
              setEvalView(v);
            })
            .catch(() => {});
        } else {
          setEvalView(null);
        }
      })
      .catch(() => {
        if (reqToken.current !== token) return;
        setErr(true);
        setLoadedId(id);
      });
  }, [id, workspaceId]);

  useEffect(() => {
    load();
  }, [load]);

  const retry = useCallback(() => {
    setErr(false);
    setLoadedId(null);
    load();
  }, [load]);

  const concept = data?.concept ?? null;
  const evaluation = data?.evaluation ?? null;
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
    const state = evaluation?.state;
    const meta =
      (concept.subject ? `${concept.subject}·` : "") +
      `${tr("difficulty")}${concept.difficulty}` +
      (state ? ` · ${et(lang, `eval.state.${state}`)}` : "");
    return tr("drawer.learn.msg").replace("%c", concept.name).replace("%m", meta);
  }, [concept, evaluation, tr, lang]);
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
            {evaluation?.state && evaluation.state !== "not_observed" && (
              <Badge tone={evalStateTone(evaluation.state)} dot>
                {et(lang, `eval.state.${evaluation.state}`)}
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

          {/* 统一评价面板（§14.3）：有完整概念视图时展示主张/变化/证据；
              仅有 overlay 时降级为类别徽标 + 结论文本；无区=不渲染。 */}
          {evalView && workspaceId && (
            <SemanticEvaluationPanel
              view={evalView}
              lang={lang}
              workspaceId={workspaceId}
              prerequisites={(data?.edges?.prerequisites ?? []).map((r) => ({
                id: r.id,
                name: r.name || r.id,
              }))}
              onExplain={() =>
                router.push(`/chat?q=${encodeURIComponent(tr("drawer.learn.msg").replace("%c", concept?.name ?? "").replace("%m", ""))}&send=1`)
              }
            />
          )}
          {!evalView && evaluation?.state && evaluation.statement && (
            <Section title={lang === "en" ? "Evaluation" : "学习评价"}>
              <p className="text-xs leading-relaxed text-fg-secondary">{evaluation.statement}</p>
            </Section>
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
