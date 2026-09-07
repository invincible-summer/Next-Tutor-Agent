"use client";
// 教材解析调度策略面板：只改执行并发（并行/串行模式 + 三档并发上限），
// 解析方式与产出不变；运行状态展示当前生效值。
import { useEffect, useState } from "react";
import { Activity, Check, ChevronDown, Gauge, Save } from "lucide-react";
import {
  getAdminTextbookPipelinePolicy,
  setAdminTextbookPipelinePolicy,
  type AdminTextbookPipelinePolicy,
} from "@/lib/api";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { Field, inputCls, type Tr } from "./Field";

export function TextbookPipelinePanel({ tr }: { tr: Tr }) {
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const [policy, setPolicy] = useState<AdminTextbookPipelinePolicy | null>(null);
  const [mode, setMode] = useState<AdminTextbookPipelinePolicy["mode"]>("parallel");
  const [build, setBuild] = useState(2);
  const [volume, setVolume] = useState(2);
  const [llm, setLlm] = useState(4);

  useEffect(() => {
    getAdminTextbookPipelinePolicy().then((p) => {
      setPolicy(p);
      setMode(p.mode);
      setBuild(p.build_concurrency);
      setVolume(p.volume_concurrency);
      setLlm(p.llm_concurrency);
    }).catch(() => undefined);
  }, []);

  const apply = async () => {
    setBusy(true);
    try {
      setPolicy(await setAdminTextbookPipelinePolicy({
        mode,
        build_concurrency: build,
        volume_concurrency: volume,
        llm_concurrency: llm,
      }));
      setApplied(true);
      setTimeout(() => setApplied(false), 1800);
    } finally {
      setBusy(false);
    }
  };

  const modeLabels: Record<AdminTextbookPipelinePolicy["mode"], string> = {
    parallel: tr("adm.pipeline.mode.parallel"),
    legacy: tr("adm.pipeline.mode.legacy"),
  };
  const clampNum = (v: string, lo: number, hi: number) =>
    Math.max(lo, Math.min(hi, Number(v)));
  const stats: { label: string; value: string }[] = [
    { label: tr("adm.pipeline.stat.mode"), value: policy ? modeLabels[policy.mode] : "—" },
    { label: tr("adm.pipeline.stat.build"), value: String(policy?.effective_limits.build ?? "—") },
    { label: tr("adm.pipeline.stat.volume"), value: String(policy?.effective_limits.volume ?? "—") },
    { label: tr("adm.pipeline.stat.llm"), value: String(policy?.effective_limits.llm ?? "—") },
    { label: tr("adm.pipeline.stat.gateActive"), value: String(policy?.gate_active ?? 0) },
    { label: tr("adm.pipeline.stat.gateWaiting"), value: String(policy?.gate_waiting ?? 0) },
  ];

  return (
    <Card>
      <CardHeader
        icon={<Gauge size={16} />}
        title={tr("adm.pipeline.title")}
        desc={tr("adm.pipeline.desc")}
        right={
          <button
            type="button"
            onClick={() => setShowDetail(!showDetail)}
            className="flex cursor-pointer items-center gap-1 text-xs text-muted transition-colors hover:text-fg">
            {tr("adm.pipeline.detail")}
            <ChevronDown size={13} className={cn("transition-transform", showDetail && "rotate-180")} />
          </button>
        }
      />
      {showDetail && (
        <p className="mb-3 rounded-[8px] border border-border-light bg-surface-sunken px-3 py-2 text-[0.7rem] leading-relaxed text-fg-secondary">
          {tr("adm.pipeline.hint")}
        </p>
      )}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Field label={tr("adm.pipeline.mode")} helper={tr("adm.pipeline.mode.hint")}>
          <select value={mode}
            onChange={(e) => setMode(e.target.value as AdminTextbookPipelinePolicy["mode"])}
            className={inputCls}>
            <option value="parallel">{modeLabels.parallel}</option>
            <option value="legacy">{modeLabels.legacy}</option>
          </select>
        </Field>
        <Field label={tr("adm.pipeline.build")} helper={tr("adm.pipeline.build.hint")}>
          <input type="number" min={1} max={policy?.max_build_concurrency ?? 4} value={build}
            onChange={(e) => setBuild(clampNum(e.target.value, 1, policy?.max_build_concurrency ?? 4))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.pipeline.volume")} helper={tr("adm.pipeline.volume.hint")}>
          <input type="number" min={1} max={policy?.max_volume_concurrency ?? 4} value={volume}
            onChange={(e) => setVolume(clampNum(e.target.value, 1, policy?.max_volume_concurrency ?? 4))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.pipeline.llm")} helper={tr("adm.pipeline.llm.hint")}>
          <input type="number" min={1} max={policy?.max_llm_concurrency ?? 8} value={llm}
            onChange={(e) => setLlm(clampNum(e.target.value, 1, policy?.max_llm_concurrency ?? 8))}
            className={inputCls} />
        </Field>
      </div>
      <div className="mb-2 mt-4 flex items-center gap-1.5 text-xs font-medium text-fg">
        <Activity size={13} className="text-accent" />
        {tr("adm.pipeline.status")}
      </div>
      <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
        {stats.map((s) => (
          <div key={s.label} className="rounded-[8px] border border-border-light bg-surface-sunken px-2.5 py-2">
            <div className="truncate text-[10px] text-muted" title={s.label}>{s.label}</div>
            <div className="tnum mt-1 truncate text-sm font-medium text-fg">{s.value}</div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-end">
        <Button size="sm" disabled={busy} onClick={() => void apply()}
          icon={applied ? <Check size={13} /> : <Save size={13} />}>
          {applied ? tr("adm.pipeline.applied") : tr("adm.pipeline.apply")}
        </Button>
      </div>
    </Card>
  );
}
