"use client";
import { navigationAnchor, navigationSucceeded, navigationUnavailable } from "@/lib/assistant/navigation";

// /admin 管理台（P6-B4）：页签分区——账号与数据 / 生命周期与记忆 / OCR 与解析策略 /
// 公共库归档 / 数据清理。仅 role=admin 可见（导航入口隐藏；后端 /admin/* 有
// require_admin 硬门）。各面板自包含加载与操作。
import { Suspense, useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { getAdminUsers, type AdminUser, type AdminUsersResponse } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Tabs } from "@/components/ui/Tabs";
import { useAuthStore } from "@/lib/auth-store";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import {
  DeepLinkQueryReader, focusDeepTarget,
} from "@/lib/assistant/deep-link";
import { AccountsPanel } from "@/components/pages/admin/AccountsPanel";
import { PolicyPanel } from "@/components/pages/admin/PolicyPanel";
import { EvaluationPolicyPanel } from "@/components/pages/admin/EvaluationPolicyPanel";
import { OcrPanel } from "@/components/pages/admin/OcrPanel";
import { TextbookPipelinePanel } from "@/components/pages/admin/TextbookPipelinePanel";
import { LLMRunPolicyPanel } from "@/components/pages/admin/LLMRunPolicyPanel";
import { TrashPanel } from "@/components/pages/admin/TrashPanel";
import { CleanupPanel } from "@/components/pages/admin/CleanupPanel";
import { GuestCleanupPanel, GuestPolicyPanel } from "@/components/pages/admin/GuestPanels";
import { STRINGS } from "./strings";

/** §20.3 admin section → 页签；面板级 section 先切页签再锚点定位。 */
const SECTION_TAB: Record<string, string> = {
  users: "accounts",
  orphan_data: "cleanup",
  ocr: "ocr",
  textbook_pipeline: "ocr",
  llm: "runtime",
  learner_evaluation: "policy",
  prompt_memory: "policy",
  classroom_health: "policy",
};

export default function AdminPage() {
  const isAdmin = useAuthStore((s) => s.user?.role === "admin");
  const authLoaded = useAuthStore((s) => s.loaded);
  const lang = useUIStore((s) => s.lang);
  const tr = makePageT(lang, STRINGS);
  const [tab, setTab] = useState("accounts");
  // 助手深链（§20.1 管理台）：?section= 切页签并定位面板卡片；管理台只对
  // admin 开放，危险操作仍在本页原确认流程完成（§22.5）。
  const [deepSection, setDeepSection] = useState("");
  const [deepNotice, setDeepNotice] = useState("");
  useAssistantPage({
    navigationStatus: (target) => {
      if (!isAdmin) return navigationUnavailable;
      if (target.kind === "module") return navigationSucceeded;
      return target.kind === "admin_section" ? navigationAnchor("admin-section", target.section) : null;
    },
    context: () => ({
      schema_version: 1,
      route_id: "admin",
      route_epoch: currentRouteEpoch(),
      view: tab,
    }),
  });
  const applyDeepLink = useCallback((params: Record<string, string>) => {
    setDeepSection(params.section || "");
  }, []);
  useEffect(() => {
    if (!deepSection) return;
    const section = deepSection;
    // 消费走微任务：避免 effect 体内同步 setState 的级联渲染
    // （react-hooks/set-state-in-effect）。
    let alive = true;
    queueMicrotask(() => {
      if (!alive) return;
      setDeepSection("");
      const wantTab = SECTION_TAB[section];
      if (!wantTab) {
        setDeepNotice(tr("adm.deep.missing"));
        return;
      }
      if (tab !== wantTab) setTab(wantTab);
      window.setTimeout(() => {
        if (!focusDeepTarget("admin-section", section)) {
          // 页签级 section（accounts/cleanup 等）没有单独面板锚点，
          // 切到页签即完成定位；仅面板级 section 缺失才提示。
          if (["textbook_pipeline", "learner_evaluation", "prompt_memory",
               "classroom_health"].includes(section)) {
            setDeepNotice(tr("adm.deep.missing"));
          }
        }
      }, 60);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepSection]);

  // 账号数据在壳层加载：页签 badge 与账号面板共用一次请求。
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [summary, setSummary] = useState<AdminUsersResponse["summary"]>({ count: 0, total_bytes: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [trashCount, setTrashCount] = useState(0);

  const refresh = useCallback(() => {
    getAdminUsers()
      .then((r) => { setUsers(r.users); setSummary(r.summary); setError(null); })
      .catch(() => setError(tr("adm.users.loadFail")))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // 仅 admin 会话发起账号请求：非 admin 直接输入 URL 时不打注定 403 的
    // 请求（loading 状态只服务于守卫放行后的账号面板，无需在此复位）。
    if (isAdmin) refresh();
  }, [isAdmin, refresh]);

  // 角色守卫（UI 层）：后端 /admin/* 已有 require_admin 硬门，这里挡的是
  // 非管理员直接输入 URL 时管理台外壳（页签/面板结构）的暴露。水合未完成
  // 前不渲染管理结构，防止"先闪现再拒绝"。
  if (!authLoaded || !isAdmin) {
    return (
      <div className="page-in mx-auto flex h-full w-full max-w-[1200px] flex-col items-center justify-center gap-2 p-6 text-center">
        <ShieldCheck size={22} className="text-accent" />
        <p className="text-sm font-medium text-fg">
          {authLoaded ? tr("adm.guard.denied") : tr("adm.guard.loading")}
        </p>
        <p className="text-xs text-muted">
          {authLoaded ? tr("adm.guard.deniedDesc") : ""}
        </p>
      </div>
    );
  }

  const items = [
    { key: "accounts", label: tr("adm.tab.accounts"),
      badge: <Badge tone="muted">{summary.count}</Badge> },
    { key: "runtime", label: tr("adm.tab.runtime") },
    { key: "policy", label: tr("adm.tab.policy") },
    { key: "ocr", label: tr("adm.tab.ocr") },
    { key: "trash", label: tr("adm.tab.trash"),
      badge: trashCount > 0 ? <Badge tone="muted">{trashCount}</Badge> : undefined },
    { key: "cleanup", label: tr("adm.tab.cleanup") },
  ];

  return (
    <div className="page-in mx-auto flex h-full w-full max-w-[1200px] flex-col gap-4 overflow-y-auto p-6">
      <Suspense><DeepLinkQueryReader keys={["section"]} onParams={applyDeepLink} /></Suspense>
      <header>
        <h1 className="flex items-center gap-2 font-serif text-xl font-bold text-fg">
          <ShieldCheck size={20} className="text-accent" />
          {tr("adm.title")}
        </h1>
        <p className="mt-1 text-xs text-muted">{tr("adm.desc")}</p>
      </header>

      {deepNotice && (
        <div className="rounded-[8px] border border-border bg-surface px-3 py-2 text-xs text-muted">{deepNotice}</div>
      )}

      <Tabs items={items} active={tab} onChange={setTab} />

      {tab === "accounts" && (
        <div data-admin-section="users" className="space-y-4">
          <GuestPolicyPanel tr={tr} />
          <AccountsPanel tr={tr} users={users} summary={summary}
            loading={loading} error={error} refresh={refresh} />
        </div>
      )}
      {tab === "runtime" && (
        <div data-admin-section="llm"><LLMRunPolicyPanel tr={tr} /></div>
      )}
      {tab === "policy" && (
        <div className="flex flex-col gap-4">
          <div data-admin-section="learner_evaluation">
            <EvaluationPolicyPanel tr={tr} />
          </div>
          <div data-admin-section="prompt_memory">
            <PolicyPanel tr={tr} />
          </div>
        </div>
      )}
      {tab === "ocr" && (
        <div className="flex flex-col gap-4">
          <div data-admin-section="ocr"><OcrPanel tr={tr} /></div>
          <div data-admin-section="textbook_pipeline">
            <TextbookPipelinePanel tr={tr} />
          </div>
        </div>
      )}
      {tab === "trash" && <TrashPanel tr={tr} onCount={setTrashCount} />}
      {tab === "cleanup" && (
        <div data-admin-section="orphan_data" className="space-y-4">
          <GuestCleanupPanel tr={tr} />
          <CleanupPanel tr={tr} refresh={refresh} />
        </div>
      )}
    </div>
  );
}
