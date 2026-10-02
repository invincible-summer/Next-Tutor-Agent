"use client";

// 助手设置卡（plan.md §25.5/§26.1，C05；供画像页复用）。
// 偏好白名单（§24.6）经 GET/PUT /assistant/preferences，base_revision
// 乐观并发；订阅管理（§25.1/§25.2）默认关闭、逐项开启；「最近投递」
// 打开助手收件箱。本地 UI 偏好（主题/字号/语言）不在此处，作用范围
// 说明与 server prefs 分开显示（§22.4）。
import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useAssistantStore } from "@/lib/assistant/store";
import { Field, Input } from "@/components/ui/Input";
import { Hint } from "@/components/ui/Hint";
import { useToast } from "@/components/ui/Toast";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/settings/strings";
import {
  AssistantApiError,
  createAssistantSubscription,
  deleteAssistantSubscription,
  getAssistantPreferences,
  listAssistantSubscriptions,
  patchAssistantSubscription,
  putAssistantPreferences,
  type AssistantPreferencesPayload,
  type AssistantSubscription,
} from "@/lib/assistant/api";
import { stringsFor } from "./strings";

const SUB_KINDS = ["weekly_brief", "daily_tasks", "due_reviews",
                   "unfinished_course"] as const;

export function AssistantSettings() {
  const lang = useAssistantStore((s) => s.lang);
  const openAssistant = useAssistantStore((s) => s.open);
  const setAssistantView = useAssistantStore((s) => s.setView);
  const t = stringsFor(lang);
  const st = makePageT(lang, STRINGS);
  const notify = useToast();
  const help = (label: string, text: string) => <Hint label={st("settings.help").replace("{label}", label)} text={text} align="end" />;
  const fieldLabel = (label: string, hint: string) => <span className="inline-flex items-center gap-2">{label}{help(label, hint)}</span>;

  const [prefs, setPrefs] = useState<AssistantPreferencesPayload | null>(
    null);
  const [subs, setSubs] = useState<AssistantSubscription[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [newKind, setNewKind] = useState<string>("weekly_brief");
  const [newTime, setNewTime] = useState("09:00");

  const reload = useCallback(async () => {
    try {
      const [p, s] = await Promise.all([
        getAssistantPreferences(), listAssistantSubscriptions(),
      ]);
      setPrefs(p);
      setSubs(s.items);
      setError("");
    } catch (err) {
      if (!(err instanceof AssistantApiError && err.status === 401)) {
        setError(String((err as Error)?.message ?? err));
      }
    }
  }, [setError]);

  useEffect(() => {
    // 消费走微任务：避免 effect 体内同步 setState 的级联渲染。
    let alive = true;
    queueMicrotask(() => {
      if (alive) void reload();
    });
    return () => {
      alive = false;
    };
  }, [reload]);

  const savePrefs = async (
      patch: Partial<AssistantPreferencesPayload>) => {
    if (!prefs || busy) return;
    setBusy(true);
    try {
      const merged = await putAssistantPreferences({
        ...patch,
        base_revision: Number(prefs.base_revision
          ?? prefs.revision ?? 1),
      });
      setPrefs(merged);
      notify(t.settingsSaved);
      setError("");
    } catch (err) {
      if (err instanceof AssistantApiError
          && err.code === "revision_conflict") {
        notify(t.settingsConflict, "error");
      } else {
        notify(String((err as Error)?.message ?? err), "error");
      }
    } finally {
      setBusy(false);
    }
  };

  type LengthPref = "short" | "standard" | "detailed";
  type TonePref = "neutral" | "encouraging";
  type ScopePref = "follow_page" | "all_workspaces";
  const selectLength = (value: LengthPref) =>
    void savePrefs({ response_length: value });
  const selectTone = (value: TonePref) =>
    void savePrefs({ tone: value });
  const selectScope = (value: ScopePref) =>
    void savePrefs({ default_scope: value });

  const openInbox = () => {
    openAssistant();
    setAssistantView("inbox");
  };

  const changeSubscription = async (action: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      await reload();
      notify(st("settings.updated"));
    } catch (err) {
      notify(String((err as Error)?.message ?? err), "error");
    } finally {
      setBusy(false);
    }
  };

  if (!prefs) {
    return (
      <div className="assistant-settings-card">
        <h3>{t.settingsTitle}</h3>
        <p className="assistant-settings-hint">
          {error || t.inboxLoading}
        </p>
      </div>
    );
  }

  return (
    <div className="assistant-settings-card" aria-busy={busy}>
      <h3>{t.settingsTitle}</h3>
      {error && <p className="assistant-task-error" role="alert">{error}</p>}

      <div className="assistant-settings-grid">
        <Field label={fieldLabel(t.settingsResponseLength, st("settings.assistant.length.hint"))}>
          <select
            aria-label={t.settingsResponseLength}
            className="assistant-settings-select"
            value={String(prefs.response_length ?? "standard")}
            disabled={busy}
            onChange={(e) => selectLength(
              e.target.value as LengthPref)}
          >
            <option value="short">{t.settingsLengthShort}</option>
            <option value="standard">{t.settingsLengthStandard}</option>
            <option value="detailed">{t.settingsLengthDetailed}</option>
          </select>
        </Field>
        <Field label={fieldLabel(t.settingsTone, st("settings.assistant.tone.hint"))}>
          <select
            aria-label={t.settingsTone}
            className="assistant-settings-select"
            value={String(prefs.tone ?? "neutral")}
            disabled={busy}
            onChange={(e) => selectTone(
              e.target.value as TonePref)}
          >
            <option value="neutral">{t.settingsToneNeutral}</option>
            <option value="encouraging">{t.settingsToneEncouraging}</option>
          </select>
        </Field>
        <Field label={fieldLabel(t.settingsDefaultScope, st("settings.assistant.scope.hint"))}>
          <select
            aria-label={t.settingsDefaultScope}
            className="assistant-settings-select"
            value={String(prefs.default_scope ?? "follow_page")}
            disabled={busy}
            onChange={(e) => selectScope(
              e.target.value as ScopePref)}
          >
            <option value="follow_page">{t.settingsScopeFollowPage}</option>
            <option value="all_workspaces">{t.settingsScopeAll}</option>
          </select>
        </Field>
      </div>

      <div className="assistant-settings-proactive">
        <div className="flex items-center gap-2"><label>
          <Input
            type="checkbox"
            checked={Boolean(prefs.proactive_enabled)}
            disabled={busy}
            onChange={(e) => void savePrefs({
              proactive_enabled: e.target.checked,
            })}
          />
          {t.settingsProactive}
        </label>{help(t.settingsProactive, t.settingsProactiveHint)}</div>
      </div>

      <h4 className="flex items-center gap-2">{t.settingsSubscriptions}{help(t.settingsSubscriptions, st("settings.assistant.subscriptions.hint"))}</h4>
      {subs.length === 0 && (
        <p className="assistant-settings-hint">{t.inboxEmpty}</p>
      )}
      {subs.map((sub) => (
        <div key={sub.subscription_id}
             className="assistant-settings-sub">
          <div className="assistant-settings-sub-main">
            <span className="assistant-settings-sub-name">
              {t.settingsSubKind[sub.kind] ?? sub.kind}
            </span>
            <span className="assistant-task-meta">
              {t.settingsSubTime} {sub.local_time}
              {" · "}
              {t.settingsSubWeekdays}：
              {sub.weekdays.map((d) => t.weekdayShort[d] ?? d).join("")}
              {" · "}
              {t.settingsSubNextRun}
              {String(sub.next_run_at ?? "").slice(0, 16).replace("T", " ")}
            </span>
          </div>
          <div className="flex items-center gap-1.5"><label className="assistant-settings-sub-enabled">
            <Input
              type="checkbox"
              checked={sub.enabled}
              disabled={busy}
              onChange={(e) => {
                const enabled = e.target.checked;
                void changeSubscription(() => patchAssistantSubscription(sub.subscription_id, {
                  expected_revision: sub.revision,
                  enabled,
                }));
              }}
            />
            {t.settingsSubEnabled}
          </label>{help(t.settingsSubEnabled, st("settings.assistant.enabled.hint"))}</div>
          <button
            type="button"
            className="assistant-task-mini-btn"
            aria-label={t.settingsSubUnsubscribe}
            title={t.settingsSubUnsubscribe}
            disabled={busy}
            onClick={() => {
              void changeSubscription(() => deleteAssistantSubscription(sub.subscription_id));
            }}
          >
            <Trash2 size={12} aria-hidden />
          </button>
          {help(t.settingsSubUnsubscribe, st("settings.assistant.unsubscribe.hint"))}
        </div>
      ))}

      <div className="assistant-settings-add">
        <div className="inline-flex items-center gap-1.5"><select
          className="assistant-settings-select"
          value={newKind}
          disabled={busy}
          onChange={(e) => setNewKind(e.target.value)}
          aria-label={t.settingsSubCreate}
        >
          {SUB_KINDS.map((k) => (
            <option key={k} value={k}>{t.settingsSubKind[k]}</option>
          ))}
        </select>{help(t.settingsSubCreate, st("settings.assistant.kind.hint"))}</div>
        <div className="inline-flex items-center gap-1.5"><Input
          type="time"
          value={newTime}
          disabled={busy}
          onChange={(e) => setNewTime(e.target.value)}
          aria-label={t.settingsSubTime}
        />{help(t.settingsSubTime, st("settings.assistant.time.hint"))}</div>
        <button
          type="button"
          className="assistant-btn-outline"
          disabled={busy}
          onClick={() => {
            void changeSubscription(() => createAssistantSubscription({
              client_request_id: crypto.randomUUID(),
              kind: newKind,
              timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
                || "UTC",
              local_time: newTime,
            }));
          }}
        >
          <Plus size={13} aria-hidden />
          {t.settingsSubCreate}
        </button>
        {help(t.settingsSubCreate, st("settings.assistant.create.hint"))}
      </div>
      {subs.length > 0 && subs.every((s) => !s.enabled) && (
        <p className="assistant-settings-hint">{t.settingsSchedulerOff}</p>
      )}

      <div className="flex items-center gap-2"><button
        type="button"
        className="assistant-btn-outline"
        onClick={openInbox}
      >
        {t.settingsViewInbox}
      </button>{help(t.settingsViewInbox, st("settings.assistant.inbox.hint"))}</div>
    </div>
  );
}
