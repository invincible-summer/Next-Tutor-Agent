"use client";
// /admin 管理台（P6-B4）：页签分区——账号与数据 / 生命周期与记忆 / OCR 与解析策略 /
// 公共库归档 / 数据清理。仅 role=admin 可见（导航入口隐藏；后端 /admin/* 有
// require_admin 硬门）。各面板自包含加载与操作。
import { useCallback, useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { getAdminUsers, type AdminUser, type AdminUsersResponse } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Tabs } from "@/components/ui/Tabs";
import { AccountsPanel } from "@/components/pages/admin/AccountsPanel";
import { PolicyPanel } from "@/components/pages/admin/PolicyPanel";
import { OcrPanel } from "@/components/pages/admin/OcrPanel";
import { TextbookPipelinePanel } from "@/components/pages/admin/TextbookPipelinePanel";
import { TrashPanel } from "@/components/pages/admin/TrashPanel";
import { CleanupPanel } from "@/components/pages/admin/CleanupPanel";
import { STRINGS } from "./strings";

export default function AdminPage() {
  const lang = useUIStore((s) => s.lang);
  const tr = makePageT(lang, STRINGS);
  const [tab, setTab] = useState("accounts");

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
    refresh();
  }, [refresh]);

  const items = [
    { key: "accounts", label: tr("adm.tab.accounts"),
      badge: <Badge tone="muted">{summary.count}</Badge> },
    { key: "policy", label: tr("adm.tab.policy") },
    { key: "ocr", label: tr("adm.tab.ocr") },
    { key: "trash", label: tr("adm.tab.trash"),
      badge: trashCount > 0 ? <Badge tone="muted">{trashCount}</Badge> : undefined },
    { key: "cleanup", label: tr("adm.tab.cleanup") },
  ];

  return (
    <div className="page-in mx-auto flex h-full w-full max-w-[1200px] flex-col gap-4 overflow-y-auto p-6">
      <header>
        <h1 className="flex items-center gap-2 font-serif text-xl font-bold text-fg">
          <ShieldCheck size={20} className="text-accent" />
          {tr("adm.title")}
        </h1>
        <p className="mt-1 text-xs text-muted">{tr("adm.desc")}</p>
      </header>

      <Tabs items={items} active={tab} onChange={setTab} />

      {tab === "accounts" && (
        <AccountsPanel tr={tr} users={users} summary={summary}
          loading={loading} error={error} refresh={refresh} />
      )}
      {tab === "policy" && <PolicyPanel tr={tr} />}
      {tab === "ocr" && (
        <div className="flex flex-col gap-4">
          <OcrPanel tr={tr} />
          <TextbookPipelinePanel tr={tr} />
        </div>
      )}
      {tab === "trash" && <TrashPanel tr={tr} onCount={setTrashCount} />}
      {tab === "cleanup" && <CleanupPanel tr={tr} refresh={refresh} />}
    </div>
  );
}
