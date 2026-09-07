"use client";
// 生命周期与记忆策略面板：回收站保留 + 提示词记忆窗口（功能与原卡片一致）。
import { useEffect, useState } from "react";
import { Check, Save, SlidersHorizontal } from "lucide-react";
import { getAdminPromptMemoryPolicy, getAdminRetentionPolicy, setAdminPromptMemoryPolicy, setAdminRetentionPolicy } from "@/lib/api";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Field, inputCls, type Tr } from "./Field";

export function PolicyPanel({ tr }: { tr: Tr }) {
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [retention, setRetention] = useState({ default_days: 7, user_max_days: 30, forced_max_days: 30, mode: "auto", cleanup_interval_seconds: 3600 });
  const [promptPolicy, setPromptPolicy] = useState({ default_window: 15, max_window: 30, core_char_limit: 1800, directive_char_limit: 2600 });

  useEffect(() => {
    Promise.all([getAdminRetentionPolicy(), getAdminPromptMemoryPolicy()])
      .then(([r, p]) => {
        setRetention(r as typeof retention);
        setPromptPolicy(p as typeof promptPolicy);
      })
      .catch(() => undefined);
  }, []);

  const save = async () => {
    setBusy(true);
    try {
      await setAdminRetentionPolicy(retention);
      await setAdminPromptMemoryPolicy(promptPolicy);
      setSaved(true);
      setTimeout(() => setSaved(false), 1800);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader
        icon={<SlidersHorizontal size={16} />}
        title={tr("adm.policy.title")}
        desc={tr("adm.policy.desc")}
      />
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-3 rounded-[8px] border border-border-light p-3">
          <div className="flex items-center gap-1.5 text-xs font-medium text-fg">
            {tr("adm.policy.trash")}
          </div>
          <div className="grid grid-cols-3 gap-2">
            <Field label={tr("adm.policy.trash.defaultDays")} helper={tr("adm.policy.trash.defaultDays.hint")}>
              <input type="number" min={1} max={30} value={retention.default_days}
                onChange={(e) => setRetention({ ...retention, default_days: Number(e.target.value) })}
                className={inputCls} />
            </Field>
            <Field label={tr("adm.policy.trash.userMaxDays")} helper={tr("adm.policy.trash.userMaxDays.hint")}>
              <input type="number" min={1} max={30} value={retention.user_max_days}
                onChange={(e) => setRetention({ ...retention, user_max_days: Number(e.target.value) })}
                className={inputCls} />
            </Field>
            <Field label={tr("adm.policy.trash.forcedMaxDays")} helper={tr("adm.policy.trash.forcedMaxDays.hint")}>
              <input type="number" min={1} max={365} value={retention.forced_max_days}
                onChange={(e) => setRetention({ ...retention, forced_max_days: Number(e.target.value) })}
                className={inputCls} />
            </Field>
          </div>
          <Field label={tr("adm.policy.trash.mode")} helper={tr("adm.policy.trash.mode.hint")}>
            <select value={retention.mode} onChange={(e) => setRetention({ ...retention, mode: e.target.value })}
              className={inputCls}>
              <option value="auto">{tr("adm.policy.trash.mode.auto")}</option>
              <option value="manual">{tr("adm.policy.trash.mode.manual")}</option>
            </select>
          </Field>
        </div>
        <div className="space-y-3 rounded-[8px] border border-border-light p-3">
          <div className="flex items-center gap-1.5 text-xs font-medium text-fg">
            {tr("adm.policy.memory")}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label={tr("adm.policy.memory.defaultWindow")} helper={tr("adm.policy.memory.defaultWindow.hint")}>
              <input type="number" min={5} value={promptPolicy.default_window}
                onChange={(e) => setPromptPolicy({ ...promptPolicy, default_window: Number(e.target.value) })}
                className={inputCls} />
            </Field>
            <Field label={tr("adm.policy.memory.maxWindow")} helper={tr("adm.policy.memory.maxWindow.hint")}>
              <input type="number" min={5} value={promptPolicy.max_window}
                onChange={(e) => setPromptPolicy({ ...promptPolicy, max_window: Number(e.target.value) })}
                className={inputCls} />
            </Field>
            <Field label={tr("adm.policy.memory.coreLimit")} helper={tr("adm.policy.memory.coreLimit.hint")}>
              <input type="number" min={1} value={promptPolicy.core_char_limit}
                onChange={(e) => setPromptPolicy({ ...promptPolicy, core_char_limit: Number(e.target.value) })}
                className={inputCls} />
            </Field>
            <Field label={tr("adm.policy.memory.directiveLimit")} helper={tr("adm.policy.memory.directiveLimit.hint")}>
              <input type="number" min={1} value={promptPolicy.directive_char_limit}
                onChange={(e) => setPromptPolicy({ ...promptPolicy, directive_char_limit: Number(e.target.value) })}
                className={inputCls} />
            </Field>
          </div>
        </div>
      </div>
      <div className="mt-3 flex justify-end">
        <Button size="sm" onClick={() => void save()} disabled={busy}
          icon={saved ? <Check size={13} /> : <Save size={13} />}>
          {saved ? tr("adm.policy.saved") : tr("adm.policy.save")}
        </Button>
      </div>
    </Card>
  );
}
