"use client";
// 教材后台 OCR 策略面板：参数 + 运行状态（功能与原卡片一致；超长说明改为
// 卡片内「详细说明」折叠块，不再用悬浮气泡）。
import { useEffect, useState } from "react";
import { Activity, Check, ChevronDown, Save, ScanLine } from "lucide-react";
import { getAdminOCRPolicy, setAdminOCRPolicy, type AdminOCRPolicy } from "@/lib/api";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { Field, inputCls, type Tr } from "./Field";

export function OcrPanel({ tr }: { tr: Tr }) {
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const [ocrPolicy, setOCRPolicy] = useState<AdminOCRPolicy | null>(null);
  const [concurrency, setConcurrency] = useState(20);
  const [failureMode, setFailureMode] = useState<AdminOCRPolicy["failure_mode"]>("persistent_api");
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [retryInterval, setRetryInterval] = useState(10);
  const [requestTimeout, setRequestTimeout] = useState(60);

  useEffect(() => {
    getAdminOCRPolicy().then((ocr) => {
      setOCRPolicy(ocr);
      setConcurrency(ocr.configured_concurrency);
      setFailureMode(ocr.failure_mode);
      setMaxAttempts(ocr.max_attempts);
      setRetryInterval(ocr.retry_interval_seconds);
      setRequestTimeout(ocr.request_timeout_seconds);
    }).catch(() => undefined);
  }, []);

  const apply = async () => {
    setBusy(true);
    try {
      setOCRPolicy(await setAdminOCRPolicy({
        concurrency,
        failure_mode: failureMode,
        max_attempts: maxAttempts,
        retry_interval_seconds: retryInterval,
        request_timeout_seconds: requestTimeout,
      }));
      setApplied(true);
      setTimeout(() => setApplied(false), 1800);
    } finally {
      setBusy(false);
    }
  };

  const failureModeLabels: Record<AdminOCRPolicy["failure_mode"], string> = {
    persistent_api: tr("adm.ocr.failureMode.persistentApi"),
    bounded_then_local: tr("adm.ocr.failureMode.boundedThenLocal"),
    bounded_api_only: tr("adm.ocr.failureMode.boundedApiOnly"),
  };
  const stats: { label: string; value: string }[] = [
    { label: tr("adm.ocr.stat.concurrency"), value: String(ocrPolicy?.effective_concurrency ?? "—") },
    { label: tr("adm.ocr.stat.pending"), value: String(ocrPolicy?.pending_concurrency ?? "—") },
    { label: tr("adm.ocr.stat.jobs"), value: String(ocrPolicy?.active_ocr_jobs ?? 0) },
    { label: tr("adm.ocr.stat.pages"), value: String(ocrPolicy?.active_ocr_pages ?? 0) },
    { label: tr("adm.ocr.stat.waiting"), value: String(ocrPolicy?.retry_waiting_pages ?? 0) },
    { label: tr("adm.ocr.stat.failureMode"), value: ocrPolicy ? failureModeLabels[ocrPolicy.failure_mode] : "—" },
    { label: tr("adm.ocr.stat.nextRetry"), value: ocrPolicy?.next_retry_at ? new Date(ocrPolicy.next_retry_at * 1000).toLocaleTimeString() : "—" },
    { label: tr("adm.ocr.stat.generation"), value: String(ocrPolicy?.generation ?? "—") },
    { label: tr("adm.ocr.stat.version"), value: ocrPolicy ? `v${ocrPolicy.policy_version}` : "—" },
  ];

  return (
    <Card>
      <CardHeader
        icon={<ScanLine size={16} />}
        title={tr("adm.ocr.title")}
        desc={tr("adm.ocr.desc")}
        right={
          <button
            type="button"
            onClick={() => setShowDetail(!showDetail)}
            className="flex cursor-pointer items-center gap-1 text-xs text-muted transition-colors hover:text-fg">
            {tr("adm.ocr.detail")}
            <ChevronDown size={13} className={cn("transition-transform", showDetail && "rotate-180")} />
          </button>
        }
      />
      {showDetail && (
        <p className="mb-3 rounded-[8px] border border-border-light bg-surface-sunken px-3 py-2 text-[0.7rem] leading-relaxed text-fg-secondary">
          {tr("adm.ocr.hint")}
        </p>
      )}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <Field label={tr("adm.ocr.concurrency")} helper={tr("adm.ocr.concurrency.hint")}>
          <input type="number" min={1} max={100} value={concurrency}
            onChange={(e) => setConcurrency(Math.max(1, Math.min(100, Number(e.target.value))))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.ocr.failureMode")} helper={tr("adm.ocr.failureMode.hint")}>
          <select value={failureMode}
            onChange={(e) => setFailureMode(e.target.value as AdminOCRPolicy["failure_mode"])}
            className={inputCls}>
            <option value="persistent_api">{failureModeLabels.persistent_api}</option>
            <option value="bounded_then_local">{failureModeLabels.bounded_then_local}</option>
            <option value="bounded_api_only">{failureModeLabels.bounded_api_only}</option>
          </select>
        </Field>
        <Field label={tr("adm.ocr.maxAttempts")} helper={tr("adm.ocr.maxAttempts.hint")}>
          <input type="number" min={1} max={100} value={maxAttempts}
            onChange={(e) => setMaxAttempts(Math.max(1, Math.min(100, Number(e.target.value))))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.ocr.retryInterval")} helper={tr("adm.ocr.retryInterval.hint")}>
          <input type="number" min={0} max={3600} value={retryInterval}
            onChange={(e) => setRetryInterval(Math.max(0, Math.min(3600, Number(e.target.value))))}
            className={inputCls} />
        </Field>
        <Field label={tr("adm.ocr.requestTimeout")} helper={tr("adm.ocr.requestTimeout.hint")}>
          <input type="number" min={10} max={300} value={requestTimeout}
            onChange={(e) => setRequestTimeout(Math.max(10, Math.min(300, Number(e.target.value))))}
            className={inputCls} />
        </Field>
      </div>
      <div className="mb-2 mt-4 flex items-center gap-1.5 text-xs font-medium text-fg">
        <Activity size={13} className="text-accent" />
        {tr("adm.ocr.status")}
      </div>
      <div className="grid grid-cols-3 gap-2 md:grid-cols-5 xl:grid-cols-9">
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
          {applied ? tr("adm.ocr.applied") : tr("adm.ocr.apply")}
        </Button>
      </div>
    </Card>
  );
}
