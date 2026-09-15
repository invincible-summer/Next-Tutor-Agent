"use client";
// 学习评价方式面板（update_plan §5.3）：两个互斥调度档位，默认方案 1。
// 保存走 expected_revision CAS；冲突提示刷新后重试。运行停用开关
// （LEARNER_EVALUATION_MODE）只读展示——它与 1/2 档位正交。
import { useEffect, useState } from "react";
import { CalendarClock, Check, Save, Zap } from "lucide-react";
import { getAdminEvalPolicy, setAdminEvalPolicy,
         type AdminEvalPolicyStatus } from "@/lib/api";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { Field, inputCls, type Tr } from "./Field";

type Schedule = "immediate" | "daily_midnight";

export function EvaluationPolicyPanel({ tr }: { tr: Tr }) {
  const [status, setStatus] = useState<AdminEvalPolicyStatus | null>(null);
  const [schedule, setSchedule] = useState<Schedule>("immediate");
  const [timezone, setTimezone] = useState("Asia/Singapore");
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [invalid, setInvalid] = useState("");

  const load = () => {
    getAdminEvalPolicy().then((s) => {
      setStatus(s);
      setSchedule(s.policy.evaluation_schedule);
      setTimezone(s.policy.timezone);
      setConflict(false);
      setInvalid("");
    }).catch(() => undefined);
  };
  useEffect(load, []);

  const save = async () => {
    setBusy(true);
    setInvalid("");
    try {
      await setAdminEvalPolicy({
        evaluation_schedule: schedule,
        timezone: timezone.trim(),
        daily_local_time: "00:00",
        expected_revision: status?.policy.revision ?? 1,
      });
      setApplied(true);
      setConflict(false);
      setTimeout(() => setApplied(false), 1800);
      load();   // 保存后读回确认
    } catch (e) {
      const code = (e as Error).message;
      if (code === "policy_revision_conflict") setConflict(true);
      else if (code === "policy_invalid") setInvalid(tr("adm.eval.err.invalid"));
      else setInvalid(code);
    } finally {
      setBusy(false);
    }
  };

  const nextRun = status?.next_run_utc
    ? new Date(status.next_run_utc).toLocaleString() : "—";
  const stats: { label: string; value: string }[] = [
    { label: tr("adm.eval.stat.revision"), value: `v${status?.policy.revision ?? "—"}` },
    { label: tr("adm.eval.stat.service"),
      value: status ? (status.service_enabled ? tr("adm.eval.stat.on") : tr("adm.eval.stat.off")) : "—" },
    { label: tr("adm.eval.stat.nextRun"), value: nextRun },
    { label: tr("adm.eval.stat.pending"), value: String(status?.pending_source_count ?? 0) },
    { label: tr("adm.eval.stat.oldest"),
      value: status?.oldest_pending_observed_at
        ? new Date(status.oldest_pending_observed_at).toLocaleString() : "—" },
    { label: tr("adm.eval.stat.lastBatch"),
      value: status?.last_batch
        ? `${status.last_batch.local_date} · ${tr("adm.eval.batch.evaluated")}${status.last_batch.evaluated_count}`
        : tr("adm.eval.stat.none") },
  ];

  const options: { key: Schedule; icon: typeof Zap; title: string; desc: string }[] = [
    { key: "immediate", icon: Zap, title: tr("adm.eval.opt.immediate.title"),
      desc: tr("adm.eval.opt.immediate.desc") },
    { key: "daily_midnight", icon: CalendarClock, title: tr("adm.eval.opt.daily.title"),
      desc: tr("adm.eval.opt.daily.desc") },
  ];

  return (
    <Card>
      <CardHeader title={tr("adm.eval.title")} desc={tr("adm.eval.desc")} />
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {options.map((opt) => (
          <button key={opt.key} type="button"
            onClick={() => setSchedule(opt.key)}
            className={cn(
              "rounded-[10px] border p-3 text-left transition-colors",
              schedule === opt.key
                ? "border-accent bg-accent-soft/40 ring-1 ring-accent/30"
                : "border-border-light bg-bg hover:border-accent/40",
            )}
            data-testid={`eval-schedule-${opt.key}`}>
            <span className="flex items-center gap-2 text-sm font-medium text-fg">
              <opt.icon size={15} className="text-accent" />
              {opt.title}
              {opt.key === "immediate" && (
                <Badge tone="outline">{tr("adm.eval.default")}</Badge>
              )}
            </span>
            <span className="mt-1 block text-xs leading-relaxed text-muted">
              {opt.desc}
            </span>
          </button>
        ))}
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label={tr("adm.eval.tz")}>
          <input className={inputCls} value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            placeholder="Asia/Singapore" data-testid="eval-tz-input" />
        </Field>
        <Field label={tr("adm.eval.dailyAt")}>
          <input className={inputCls} value="00:00" disabled
            aria-describedby="eval-daily-at-hint" />
          <p id="eval-daily-at-hint" className="mt-1 text-[0.7rem] text-muted">
            {tr("adm.eval.dailyAt.hint")}
          </p>
        </Field>
      </div>
      {conflict && (
        <p className="mt-2 text-xs text-danger" data-testid="eval-conflict">
          {tr("adm.eval.err.conflict")}
        </p>
      )}
      {invalid && <p className="mt-2 text-xs text-danger">{invalid}</p>}
      <div className="mt-3 flex items-center gap-2">
        <Button onClick={save} disabled={busy || !status} data-testid="eval-save">
          {applied ? <Check size={14} /> : <Save size={14} />}
          {applied ? tr("adm.eval.saved") : tr("adm.eval.save")}
        </Button>
        {conflict && (
          <Button variant="outline" onClick={load}>
            {tr("adm.eval.refresh")}
          </Button>
        )}
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1.5 border-t border-border-light pt-3 sm:grid-cols-3">
        {stats.map((s) => (
          <div key={s.label}>
            <dt className="text-[0.7rem] text-muted">{s.label}</dt>
            <dd className="text-xs font-medium text-fg">{s.value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
