import { useEffect, useState } from "react";
import { BookOpenCheck, Goal, Layers, Target } from "lucide-react";
import Link from "next/link";
import { Badge, ModuleBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Progress } from "@/components/ui/Progress";
import { getBloomProfile, getOrchPlan } from "@/lib/api-modules";
import type { Lang } from "@/lib/i18n";
import { relTime } from "@/lib/format";
import type { StudentProfileData } from "@/lib/types-modules";
import { Row } from "./Row";

type Tr = (key: string, fallback?: string) => string;

/** 字段级来源标注：这行数据从哪个智能层来。 */
function SourceTag({ label }: { label: string }) {
  return (
    <span className="ml-1.5 rounded bg-surface-sunken px-1 py-px text-[10px] font-normal leading-4 text-muted">
      {label}
    </span>
  );
}

/** 布鲁姆认知档案弱项行（L1 共享档案 /student/bloom-profile 的轻量投影）。 */
function BloomWeaknessRow({ tr }: { tr: Tr }) {
  const [weak, setWeak] = useState<{ concept: string; level_zh: string; attempts: number; rate: number }[] | null>(null);
  useEffect(() => {
    let alive = true;
    getBloomProfile()
      .then((r) => {
        if (alive && r.status === "ok") setWeak(r.weaknesses ?? []);
      })
      .catch(() => alive && setWeak([]));
    return () => {
      alive = false;
    };
  }, []);
  if (weak === null || weak.length === 0) return null;
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
        <Layers size={11} />
        {tr("m2.bloom")}
        <SourceTag label={tr("src.l1")} />
      </div>
      <div className="flex flex-wrap gap-1.5">
        {weak.slice(0, 6).map((w, i) => (
          <Badge key={i} tone="warning">
            {w.concept} · {w.level_zh}
            <span className="tnum opacity-70">
              {" "}{Math.round(w.rate * 100)}%
            </span>
          </Badge>
        ))}
      </div>
      <div className="mt-1 text-[11px] text-muted">{tr("m2.bloom.note")}</div>
    </section>
  );
}

/**
 * 学习目标区：镜像 M9 编排目标（单一真相源），替代旧 M2 profile.goals 展示。
 * M2 的 goal_set 写入仍在（对话链路冻结区），只是读侧不再展示双份数据。
 * 多目标下紧凑展示最多 3 个（各自的差距分析按 goal_id 配对）；
 * 未设目标时引导去 /orchestration 设置；拉取失败静默降级为空态。
 */
function GoalMirrorSection({ tr, lang }: { tr: Tr; lang: Lang }) {
  const [goals, setGoals] = useState<{
    title: string;
    goal_type: string;
    deadline: number;
    mastered: number;
    total: number;
    ratio: number;
    gaps: number;
    chain: boolean;
  }[] | null>(null);

  useEffect(() => {
    let alive = true;
    getOrchPlan()
      .then((r) => {
        if (!alive) return;
        const list = r.goals ?? [];
        const states = r.goal_states ?? [];
        setGoals(
          list
            .filter((g) => !!g.title)
            .map((g, i) => {
              const gs = (g.id
                ? states.find((s) => s.goal_id === g.id)
                : undefined) ?? states[i] ?? {};
              return {
                title: g.title ?? "",
                goal_type: g.goal_type ?? "",
                deadline: g.deadline ?? 0,
                mastered: gs.mastered_skills ?? 0,
                total: gs.total_skills ?? 0,
                ratio: gs.mastered_ratio ?? 0,
                gaps: (gs.gaps ?? []).length,
                chain: gs.chain_mode === "concept_chain",
              };
            }),
        );
      })
      .catch(() => alive && setGoals([]));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
        <Goal size={11} />
        {tr("m2.goals")}
        <SourceTag label={tr("src.m9")} />
      </div>
      {goals === null && <div className="text-xs text-muted">—</div>}
      {goals !== null && goals.length === 0 && (
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-muted">{tr("m2.goals.empty")}</span>
          <Link href="/orchestration">
            <Button size="sm" variant="outline" icon={<Target size={13} />}>
              {tr("m2.goals.set")}
            </Button>
          </Link>
        </div>
      )}
      {goals !== null && goals.length > 0 && (
        <div className="flex flex-col gap-2 rounded-[8px] border border-border-light bg-surface-sunken/40 p-3">
          {goals.slice(0, 3).map((goal) => (
            <div key={goal.title} className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Goal size={13} className="shrink-0 text-accent" />
                <span className="text-sm font-medium leading-snug text-fg">{goal.title}</span>
                <Badge tone="muted">{tr(`m2.goal.type.${goal.goal_type}`, goal.goal_type)}</Badge>
                {goal.chain && <Badge tone="accent">{tr("m2.goal.chain")}</Badge>}
                {goal.deadline > 0 && (
                  <span className="tnum ml-auto text-[11px] text-muted">
                    {tr("m2.goal.deadline")} {relTime(goal.deadline, lang)}
                  </span>
                )}
              </div>
              {goal.total > 0 && (
                <div className="flex items-center gap-2.5">
                  <Progress value={goal.ratio} tone="accent" className="max-w-[180px]" />
                  <span className="tnum text-xs text-fg-secondary">
                    {goal.mastered}/{goal.total}
                  </span>
                  <span className="tnum text-xs text-muted">
                    {Math.round(goal.ratio * 100)}%
                  </span>
                  {goal.gaps > 0 && (
                    <span className="tnum text-xs text-muted">
                      · {tr("m2.goal.gaps")} {goal.gaps}
                    </span>
                  )}
                </div>
              )}
            </div>
          ))}
          {goals.length > 3 && (
            <span className="tnum text-[11px] text-muted">+{goals.length - 3} …</span>
          )}
          <div>
            <Link
              href="/orchestration"
              className="text-xs text-accent transition-colors hover:text-accent-strong"
            >
              {tr("m2.goal.manage")} →
            </Link>
          </div>
        </div>
      )}
    </section>
  );
}

/**
 * 左卡：M2 学术画像 —— 学习风格（M8 反馈推断）、M9 目标镜像、布鲁姆弱项、
 * 优势点与待加强点。页脚带活跃信息与全卡数据来源行。
 */
export function AcademicCard({ profile, lang, tr }: { profile: StudentProfileData; lang: Lang; tr: Tr }) {
  const { preference, explanation_depth } = profile.learning_style;
  return (
    <Card>
      <CardHeader
        icon={<BookOpenCheck size={16} />}
        title={
          <span className="inline-flex items-center gap-2">
            {tr("m2.title")}
            <ModuleBadge id="M2" />
          </span>
        }
        desc={tr("m2.desc")}
      />

      <div className="flex flex-col gap-4">
        {/* 学习风格（由 M8 交互反馈折叠推断，M2 为单一真相源） */}
        <section>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">
            {tr("m2.style")}
          </div>
          <div className="divide-y divide-border-light">
            <Row
              label={
                <>
                  {tr("m2.preference")}
                  <SourceTag label={tr("src.m8infer")} />
                </>
              }
              value={tr(`m2.pref.${preference}`, preference)}
            />
            <Row
              label={
                <>
                  {tr("m2.depth")}
                  <SourceTag label={tr("src.m8infer")} />
                </>
              }
              value={tr(`m2.depth.${explanation_depth}`, explanation_depth)}
            />
          </div>
        </section>

        <BloomWeaknessRow tr={tr} />

        <GoalMirrorSection tr={tr} lang={lang} />

        {/* 优势 / 待加强 */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <section>
            <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
              {tr("m2.strong")}
              <SourceTag label={tr("src.m2")} />
            </div>
            <div className="flex flex-wrap gap-1.5">
              {profile.strong_points.length > 0 ? (
                profile.strong_points.map((s) => (
                  <Badge key={s} tone="success">
                    {s}
                  </Badge>
                ))
              ) : (
                <span className="text-xs text-muted">—</span>
              )}
            </div>
          </section>
          <section>
            <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
              {tr("m2.weak")}
              <SourceTag label={tr("src.m2")} />
            </div>
            <div className="flex flex-wrap gap-1.5">
              {profile.weak_points.length > 0 ? (
                profile.weak_points.map((s) => (
                  <Badge key={s} tone="danger">
                    {s}
                  </Badge>
                ))
              ) : (
                <span className="text-xs text-muted">—</span>
              )}
            </div>
          </section>
        </div>

        <div className="border-t border-border-light pt-2 text-[11px] text-muted">
          <div>
            {tr("profile.lastActive")}{" "}
            {profile.last_active ? relTime(profile.last_active, lang) : tr("profile.never")}
            <span className="mx-1.5 text-border">·</span>
            <span className="tnum">{profile.events_processed}</span> {tr("profile.events")}
          </div>
          <div className="mt-1">{tr("m2.note")}</div>
          <div className="mt-1">{tr("m2.sources")}</div>
        </div>
      </div>
    </Card>
  );
}
