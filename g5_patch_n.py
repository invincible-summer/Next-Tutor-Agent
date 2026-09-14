"""G5-3: dashboard — workspace evaluation briefs + raw-answer projection;
delete RadarCard/MasteryRing/Radar; journal-shaped RecentQuizQuestion."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
D = ROOT / "frontend/src/components/pages/dashboard"
PAGE = ROOT / "frontend/src/app/(workspace)/dashboard"

# ---------------- 1) types：RecentQuizQuestion 对齐 journal 投影 -------------
T = ROOT / "frontend/src/lib/types-modules.ts"
t = T.read_text(encoding="utf-8")
old = '''export interface RecentQuizQuestion {
  id: number;
  ts: number;
  session_id: string;
  topic: string;
  grade: string;
  type: string;
  difficulty: string;
  stem: string;
  verdict: string;
  student_answer?: string;
  source_status?: "active" | "deleted" | "independent";
  source_message?: string;
}'''
new = '''/** 跨会话最近习题（G4：learning-evidence journal 投影，ISO 时间戳）。 */
export interface RecentQuizQuestion {
  /** attempt_id（服务端身份，单次提交键）。 */
  id: string;
  ts: string;
  session_id: string;
  question_id: string;
  question_revision: number;
  topic: string;
  knowledge_point: string;
  type: string;
  stem: string;
  verdict: string;
  student_answer?: string;
  evaluation_status?: string;
  availability?: string;
}'''
assert t.count(old) == 1
T.write_text(t.replace(old, new), encoding="utf-8")
print("ok types-modules.ts")

# ---------------- 2) StatCards：覆盖计数统计（不算能力指标） -----------------
(D / "StatCards.tsx").write_text('''import { AlertTriangle, BookOpenCheck, CheckCircle2, Flame, Layers } from "lucide-react";
import { Stat } from "@/components/ui/Stat";
import type { WorkspaceEvaluationListItem } from "@/lib/types-modules";
import type { UxMotivation } from "@/lib/types";
import { fill, type Tr } from "./shared";

/** 四统计卡（plan §14.1：Dashboard 不再另算指标——数字是各学习区覆盖计数
 * 的求和与活跃天数，不是成绩或能力值）。 */
export function StatCards({
  workspaces,
  motivation,
  tr,
}: {
  workspaces: WorkspaceEvaluationListItem[];
  motivation: UxMotivation | null;
  tr: Tr;
}) {
  let observed = 0;
  let resolve = 0;
  for (const w of workspaces) {
    observed += w.coverage?.observed_concepts ?? 0;
    const by = w.coverage?.by_state ?? {};
    resolve += (by.fragile ?? 0) + (by.conflicting ?? 0);
  }
  return (
    <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
      <Stat
        icon={<Layers size={16} />}
        label={tr("stat.workspaces")}
        value={workspaces.length}
        tone="accent"
      />
      <Stat
        icon={<CheckCircle2 size={16} />}
        label={tr("stat.observed")}
        value={observed}
        tone="success"
      />
      <Stat
        icon={<AlertTriangle size={16} />}
        label={tr("stat.attention")}
        value={resolve}
        tone="warning"
      />
      <Stat
        icon={<Flame size={16} />}
        label={tr("stat.streak")}
        value={motivation?.streak_days ?? 0}
        tone="accent2"
        foot={motivation ? fill(tr("stat.active.foot"), motivation.active_days) : undefined}
      />
    </div>
  );
}
''', encoding="utf-8")
print("ok StatCards.tsx")

# ---------------- 3) AttentionCard → 学习区近况 ------------------------------
(D / "AttentionCard.tsx").write_text('''import { useState } from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge, ModuleBadge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { et, evalStateTone, type Lang } from "@/lib/evaluation-labels";
import type { WorkspaceEvaluationListItem } from "@/lib/types-modules";
import type { Tr } from "./shared";

/** 学习区近况（plan §14.1/§15.3）：各工作区的简短评价状态，点击回学习
 * 档案；Dashboard 不另算指标、不展示总体能力等级。 */
export function AttentionCard({
  workspaces,
  lang,
  tr,
}: {
  workspaces: WorkspaceEvaluationListItem[];
  lang: Lang;
  tr: Tr;
}) {
  const [open, setOpen] = useState(false);
  const rows = open ? workspaces : workspaces.slice(0, 4);
  return (
    <Card>
      <CardHeader
        icon={<ChevronRight size={16} />}
        title={tr("attention.title")}
        desc={tr("attention.desc")}
        right={<ModuleBadge id="L1" />}
      />
      {workspaces.length === 0 ? (
        <EmptyState title={tr("empty.attention")} desc={tr("empty.attention.desc")} />
      ) : (
        <div className="-mx-2 flex flex-col">
          {rows.map((w) => {
            const by = w.coverage?.by_state ?? {};
            const resolve = (by.fragile ?? 0) + (by.conflicting ?? 0);
            return (
              <Link
                key={w.workspace_id}
                href="/memory"
                className="block rounded-[8px] px-2 py-2 transition-colors hover:bg-surface-hover"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm text-fg">
                    {w.workspace_name || w.workspace_id}
                  </span>
                  <ChevronRight size={14} className="shrink-0 text-muted" />
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {resolve > 0 && (
                    <Badge tone={evalStateTone("fragile")}>
                      {et(lang, "eval.state.fragile")} {resolve}
                    </Badge>
                  )}
                  {(by.emerging ?? 0) > 0 && (
                    <Badge tone="info">
                      {et(lang, "eval.state.emerging")} {by.emerging}
                    </Badge>
                  )}
                  {(by.supported_in_scope ?? 0) > 0 && (
                    <Badge tone="success">
                      {et(lang, "eval.state.supported_in_scope")} {by.supported_in_scope}
                    </Badge>
                  )}
                  <span className="text-[0.66rem] text-muted">
                    {et(lang, "eval.state.not_observed")} {w.coverage?.not_observed_concepts ?? 0}
                  </span>
                </div>
              </Link>
            );
          })}
          {workspaces.length > 4 && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="cursor-pointer px-2 py-1.5 text-left text-xs text-accent hover:underline"
            >
              {open ? tr("attention.less") : tr("attention.more")}
            </button>
          )}
        </div>
      )}
    </Card>
  );
}
''', encoding="utf-8")
print("ok AttentionCard.tsx")

# ---------------- 4) RecentAnswersCard：journal 原始作答投影 ------------------
(D / "RecentAnswersCard.tsx").write_text('''import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { verdictTone } from "@/lib/labels";
import type { RecentQuizQuestion } from "@/lib/types-modules";
import type { Tr } from "./shared";

function verdictLabel(tr: Tr, verdict: string): string {
  if (verdict === "correct") return tr("answers.verdict.correct");
  if (verdict === "partial") return tr("answers.verdict.partial");
  if (verdict === "wrong") return tr("answers.verdict.wrong");
  return tr("answers.verdict.unknown");
}

function fmtIso(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString();
}

/** 最近作答（plan §15.3：原始作答投影——journal /quiz/recent，题目与判定
 * 是本次事实，不折算成能力档案）。 */
export function RecentAnswersCard({
  items,
  tr,
}: {
  items: RecentQuizQuestion[];
  tr: Tr;
}) {
  const rows = items.slice(0, 10);
  return (
    <Card>
      <CardHeader
        icon={<span className="text-accent">✓</span>}
        title={tr("answers.title")}
        desc={tr("answers.desc")}
      />
      {rows.length === 0 ? (
        <EmptyState title={tr("answers.empty")} desc={tr("answers.empty.desc")} />
      ) : (
        <ul className="flex flex-col divide-y divide-border" data-testid="recent-answers">
          {rows.map((r) => (
            <li key={r.id || `${r.question_id}-${r.ts}`} className="flex items-center gap-3 py-2">
              <Badge tone={verdictTone(r.verdict)}>
                {verdictLabel(tr, r.verdict)}
              </Badge>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-fg">
                  {r.knowledge_point || r.topic || r.stem}
                </div>
                <div className="mt-0.5 truncate text-[11px] text-muted">{r.stem}</div>
              </div>
              <div className="shrink-0 text-right">
                <div className="tnum text-[11px] text-muted">{fmtIso(r.ts)}</div>
                {r.evaluation_status && r.evaluation_status !== "ready" && (
                  <div className="text-[11px] text-info">{tr("answers.evaluating")}</div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
''', encoding="utf-8")
print("ok RecentAnswersCard.tsx")

# ---------------- 5) dashboard page：数据源换成评价域 ------------------------
p = PAGE / "page.tsx"
text = p.read_text(encoding="utf-8")
old = '''import {
  getLearningRecords,
  getMastery,
  getOrchPlan,
  getOrchToday,
  getTeachingLog,
} from "@/lib/api-modules";
import type {
  LearningRecordItem,
  MasteryResp,
  OrchDailyTask,
  OrchPlanSummary,
  TeachingLogResp,
} from "@/lib/types-modules";'''
new = '''import {
  getEvalWorkspaces,
  getOrchPlan,
  getOrchToday,
  getRecentQuizQuestions,
  getTeachingLog,
} from "@/lib/api-modules";
import type {
  OrchDailyTask,
  OrchPlanSummary,
  RecentQuizQuestion,
  TeachingLogResp,
  WorkspaceEvaluationListItem,
} from "@/lib/types-modules";'''
assert text.count(old) == 1
text = text.replace(old, new)
text = text.replace('import { RadarCard } from "@/components/pages/dashboard/RadarCard";\n', "")
old = '''interface DashData {
  greeting: UxGreeting | null;
  motivation: UxMotivation | null;
  mastery: MasteryResp;
  teachingLog: TeachingLogResp;
  activity: UxActivity | null;
  recentAnswers: LearningRecordItem[];
  orchPlan: OrchPlanSummary | null;
  orchToday: OrchDailyTask[];
}'''
new = '''interface DashData {
  greeting: UxGreeting | null;
  motivation: UxMotivation | null;
  /** 各学习区简短评价近况（§14.1：点击回学习档案，不另算指标）。 */
  evalWorkspaces: WorkspaceEvaluationListItem[];
  teachingLog: TeachingLogResp;
  activity: UxActivity | null;
  recentAnswers: RecentQuizQuestion[];
  orchPlan: OrchPlanSummary | null;
  orchToday: OrchDailyTask[];
}'''
assert text.count(old) == 1
text = text.replace(old, new)
old = '''    const [greeting, motivation, mastery, teachingLog, activity, recentAnswers, orchPlan, orchToday] =
      await Promise.all([
        getUxGreeting(lang, grade).catch(() => null),
        getUxMotivation().catch(() => null),
        getMastery(),
        getTeachingLog(),
        getUxActivity(14).catch(() => null),
        getLearningRecords(10).then((r) => (r.status === "ok" ? r.items : [])).catch(() => [] as LearningRecordItem[]),
        // M9 独立降级：未启用/无目标时不影响整页。
        getOrchPlan().catch(() => null),
        getOrchToday().catch(() => [] as OrchDailyTask[]),
      ]);
    return { greeting, motivation, mastery, teachingLog, activity, recentAnswers, orchPlan, orchToday };'''
new = '''    const [greeting, motivation, evalWorkspaces, teachingLog, activity, recentAnswers, orchPlan, orchToday] =
      await Promise.all([
        getUxGreeting(lang, grade).catch(() => null),
        getUxMotivation().catch(() => null),
        // 统一评价域（G5）：无证据的学习区也出现；失败不拖垮整页。
        getEvalWorkspaces(0, 100)
          .then((r) => r.items ?? [])
          .catch(() => [] as WorkspaceEvaluationListItem[]),
        getTeachingLog(),
        getUxActivity(14).catch(() => null),
        getRecentQuizQuestions()
          .then((r) => (r.status === "ok" ? r.questions : []))
          .catch(() => [] as RecentQuizQuestion[]),
        // M9 独立降级：未启用/无目标时不影响整页。
        getOrchPlan().catch(() => null),
        getOrchToday().catch(() => [] as OrchDailyTask[]),
      ]);
    return { greeting, motivation, evalWorkspaces, teachingLog, activity, recentAnswers, orchPlan, orchToday };'''
assert text.count(old) == 1
text = text.replace(old, new)
old = '''  const skills = data?.mastery.skills ?? [];
  const masteryDisabled = data?.mastery.status === "disabled";

'''
assert text.count(old) == 1
text = text.replace(old, "")
old = '''            <StatCards skills={skills} motivation={data.motivation} tr={tr} />'''
new = '''            <StatCards workspaces={data.evalWorkspaces} motivation={data.motivation} tr={tr} />'''
assert text.count(old) == 1
text = text.replace(old, new)
old = '''            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <RadarCard skills={skills} disabled={masteryDisabled} tr={tr} />
              <ActivityCard activity={data.activity} tr={tr} />
            </div>'''
new = '''            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <AttentionCard workspaces={data.evalWorkspaces} lang={lang} tr={tr} />
              <ActivityCard activity={data.activity} tr={tr} />
            </div>'''
assert text.count(old) == 1
text = text.replace(old, new)
old = '''            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <RecentCard teachingLog={data.teachingLog} lang={lang} tr={tr} />
              <AttentionCard
                skills={skills}
                disabled={masteryDisabled}
                lang={lang}
                tr={tr}
              />
            </div>'''
new = '''            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <RecentCard teachingLog={data.teachingLog} lang={lang} tr={tr} />
              <div className="flex flex-col gap-4" />
            </div>'''
assert text.count(old) == 1
text = text.replace(old, new)
p.write_text(text, encoding="utf-8")
print("ok dashboard page")

# ---------------- 6) dashboard strings ---------------------------------------
s = PAGE / "strings.ts"
st = s.read_text(encoding="utf-8")
pairs = [
    ('"stat.mastered": "已掌握概念",\n    "stat.learning": "学习中",\n    "stat.attention": "需关注",',
     '"stat.workspaces": "学习区",\n    "stat.observed": "已有证据概念",\n    "stat.attention": "待解决点",'),
    ('"radar.title": "学科掌握雷达",\n    "radar.desc": "各学科平均掌握度（p_known）",\n    ', ''),
    ('"attention.title": "需要关注",\n    "attention.desc": "存在误解或掌握度偏低的概念",',
     '"attention.title": "学习区近况",\n    "attention.desc": "各学习区的评价覆盖近况，点击查看学习档案",\n    "attention.more": "展开全部学习区",\n    "attention.less": "收起",'),
    ('"answers.title": "最近作答",\n    "answers.desc": "学习账本中最近 10 次作答结果",',
     '"answers.title": "最近作答",\n    "answers.desc": "最近 10 次习题作答（本次题目结果，不代表整体水平）",\n    "answers.evaluating": "评价中",'),
    ('"empty.attention.desc": "当前没有误解或掌握度偏低的概念，继续保持",',
     '"empty.attention.desc": "创建学习区并完成一次讲解或练习后，这里会显示评价近况",'),
    ('"stat.mastered": "Concepts mastered",\n    "stat.learning": "Learning",\n    "stat.attention": "Needs attention",',
     '"stat.workspaces": "Workspaces",\n    "stat.observed": "Concepts with evidence",\n    "stat.attention": "Points to resolve",'),
    ('"radar.title": "Subject mastery radar",\n    "radar.desc": "Average mastery per subject (p_known)",\n    ', ''),
    ('"attention.title": "Needs attention",\n    "attention.desc": "Concepts with misconceptions or low mastery",',
     '"attention.title": "Workspace briefs",\n    "attention.desc": "Per-workspace evaluation coverage; click to open the archive",\n    "attention.more": "Show all workspaces",\n    "attention.less": "Collapse",'),
    ('"empty.attention.desc": "No concepts with misconceptions or low mastery — keep it up",',
     '"empty.attention.desc": "Create a workspace and finish an explanation or practice to see evaluation briefs",'),
    ('"answers.desc": "Last 10 answers from the learning ledger",',
     '"answers.desc": "Last 10 quiz answers - this item result, not overall ability",\n    "answers.evaluating": "Evaluating",'),
]
for old, new in pairs:
    assert st.count(old) == 1, f"strings: {old[:50]!r}"
    st = st.replace(old, new)
s.write_text(st, encoding="utf-8")
print("ok dashboard strings")
