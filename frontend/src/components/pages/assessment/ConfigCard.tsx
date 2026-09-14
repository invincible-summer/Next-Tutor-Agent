"use client";
// idle 阶段：发起自适应测评的配置卡（plan §11.5）。
// 诊断对象=当前工作区教材范围内的概念（concept_keys，多选 ≤20；范围外
// 概念不可选）；purpose 是用户任务意图（adaptive/diagnose/practice），
// 不是能力事实；不再有布鲁姆焦点下拉（认知过程由任务蓝图约束）。
import { useEffect, useState } from "react";
import { Play } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { getEvalConcepts, getEvalWorkspaces } from "@/lib/api-modules";
import type { ConceptEvaluationView, WorkspaceEvaluationListItem } from "@/lib/types-modules";
import type { PageTr } from "./common";

export interface AssessmentStartIntent {
  workspaceId: string;
  conceptKeys: string[];
  purpose: "adaptive" | "diagnose" | "practice";
  count: number;
}

const MAX_CONCEPTS = 8;

export function ConfigCard({
  tr,
  busy,
  lang,
  onStart,
}: {
  tr: PageTr;
  lang: "zh" | "en";
  busy: boolean;
  onStart: (intent: AssessmentStartIntent) => void;
}) {
  const t = (zh: string, en: string) => (lang === "en" ? en : zh);
  const [workspaces, setWorkspaces] = useState<WorkspaceEvaluationListItem[] | null>(null);
  const [wsId, setWsId] = useState("");
  const [concepts, setConcepts] = useState<ConceptEvaluationView[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [purpose, setPurpose] = useState<"adaptive" | "diagnose" | "practice">("adaptive");
  const [count, setCount] = useState(6);

  useEffect(() => {
    let alive = true;
    getEvalWorkspaces(0, 100)
      .then((r) => {
        if (!alive) return;
        const items = r.items || [];
        setWorkspaces(items);
        setWsId((prev) => prev || items[0]?.workspace_id || "");
      })
      .catch(() => alive && setWorkspaces([]));
    return () => {
      alive = false;
    };
  }, []);

  // 换工作区时在渲染期清空概念选择（derive-state 模式，避免 effect 内同步 setState）。
  const [prevWsId, setPrevWsId] = useState(wsId);
  if (wsId !== prevWsId) {
    setPrevWsId(wsId);
    setConcepts(null);
    setPicked([]);
  }

  useEffect(() => {
    if (!wsId) return;
    let alive = true;
    // /concepts 契约 limit 上限 100（§11.2），超出会 422 直接砸掉概念列表。
    getEvalConcepts(wsId, { limit: 100 })
      .then((r) => alive && setConcepts(r.items || []))
      .catch(() => alive && setConcepts([]));
    return () => {
      alive = false;
    };
  }, [wsId]);

  if (workspaces === null) {
    return (
      <Card>
        <p className="py-2 text-xs text-muted">…</p>
      </Card>
    );
  }
  if (workspaces.length === 0) {
    return (
      <Card>
        <CardHeader icon={<Play size={16} />} title={tr("config.title")} />
        <EmptyState
          title={t("请先创建学习区", "Create a workspace first")}
          desc={t(
            "自适应诊断以学习区的教材范围为界；创建学习区并选择教材后即可开始。",
            "Adaptive diagnosis is bounded by the workspace textbook scope.",
          )}
        />
      </Card>
    );
  }

  const toggle = (key: string) => {
    setPicked((prev) =>
      prev.includes(key)
        ? prev.filter((k) => k !== key)
        : prev.length >= MAX_CONCEPTS
          ? prev
          : [...prev, key],
    );
  };

  const sorted = [...(concepts ?? [])].sort((a, b) => {
    const rank = (v: ConceptEvaluationView) =>
      !v.state || v.state === "not_observed" ? 1 : 0;
    return rank(a) - rank(b);
  });

  return (
    <Card>
      <CardHeader icon={<Play size={16} />} title={tr("config.title")} desc={tr("config.desc")} />
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-xs text-fg-secondary">
          {t("学习区", "Workspace")}
          <select
            value={wsId}
            onChange={(e) => setWsId(e.target.value)}
            className="h-8 rounded-[7px] border border-border bg-surface px-2 text-xs text-fg"
          >
            {workspaces.map((w) => (
              <option key={w.workspace_id} value={w.workspace_id}>
                {w.workspace_name || w.workspace_id}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-fg-secondary">
          {t("目的", "Purpose")}
          <select
            value={purpose}
            onChange={(e) => setPurpose(e.target.value as typeof purpose)}
            className="h-8 rounded-[7px] border border-border bg-surface px-2 text-xs text-fg"
          >
            <option value="adaptive">{t("自适应摸底", "Adaptive")}</option>
            <option value="diagnose">{t("针对验证", "Diagnose")}</option>
            <option value="practice">{t("练习", "Practice")}</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-fg-secondary">
          {t("题量", "Count")}
          <input
            type="number"
            min={1}
            max={20}
            value={count}
            onChange={(e) => setCount(Math.max(1, Math.min(20, Number(e.target.value) || 6)))}
            className="h-8 w-16 rounded-[7px] border border-border bg-surface px-2 text-xs text-fg"
          />
        </label>
      </div>
      <p className="mb-1.5 text-xs font-medium text-fg-secondary">
        {t("诊断概念（多选，最多 8 个）", "Concepts (multi-select, up to 8)")}
      </p>
      {concepts === null ? (
        <p className="py-2 text-xs text-muted">…</p>
      ) : (
        <div className="mb-3 flex max-h-52 flex-wrap gap-1.5 overflow-y-auto rounded-[8px] border border-border-light p-2">
          {sorted.length === 0 && (
            <p className="text-xs text-muted">{t("该学习区暂无教材概念。", "No concepts in scope.")}</p>
          )}
          {sorted.map((c) => {
            const key = c.concept_ref.key || c.concept_ref.concept_id;
            const on = picked.includes(key);
            return (
              <button
                key={key}
                type="button"
                onClick={() => toggle(key)}
                className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs transition-colors ${
                  on
                    ? "border-accent bg-accent-soft text-accent-strong"
                    : "border-border text-fg-secondary hover:border-accent"
                }`}
              >
                {c.concept_ref.display_name || c.concept_ref.concept_id}
              </button>
            );
          })}
        </div>
      )}
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-muted">
          {picked.length > 0 && (
            <Badge tone="accent" className="mr-1.5">{picked.length}</Badge>
          )}
          {t("概念来自该学习区已选教材；范围外的概念不可诊断。",
             "Concepts come from this workspace's selected textbooks.")}
        </span>
        <Button
          size="lg"
          icon={<Play size={15} />}
          disabled={busy || picked.length === 0}
          onClick={() => onStart({ workspaceId: wsId, conceptKeys: picked, purpose, count })}
        >
          {busy ? tr("config.starting") : tr("config.start")}
        </Button>
      </div>
    </Card>
  );
}
